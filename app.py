#!/usr/bin/env python3
"""clipnote-server: thin REST wrapper around the clipnote core.

Design: the server is the shared "brain" only.
- POST /v1/analyze   — video URL -> validated analysis JSON (steps + visual_guides).
- POST /v1/documents — analysis (+ optional client-captured image refs) -> markdown.
- POST /v1/reports    — one-tap issue report (JSONL append; the stateless exception).
- Frame capture is the client's job (Apple app: WKWebView, extension: canvas),
  so the server needs no ffmpeg and stays stateless.
- BYOK: the caller sends their own Gemini key in `X-Gemini-Key`; the server
  never pays for inference and stores nothing.

The clipnote core is used as an installed package (`pip install clipnote`),
with a repo fallback via CLIPNOTE_PATH (default: ../clipnote).
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

try:
    import clipnote  # noqa: F401  (pip-installed package)
except ImportError:
    CLIPNOTE_PATH = Path(os.environ.get(
        "CLIPNOTE_PATH", Path(__file__).parent.parent / "clipnote")).resolve()
    if not (CLIPNOTE_PATH / "src" / "clipnote" / "analyze.py").exists():
        raise RuntimeError(
            f"clipnote package not importable and repo not at {CLIPNOTE_PATH}; "
            "pip install clipnote or set CLIPNOTE_PATH")
    sys.path.insert(0, str(CLIPNOTE_PATH / "src"))

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from clipnote import analyze as core_analyze  # noqa: E402
from clipnote import render as core_render  # noqa: E402
from clipnote.common import video_id  # noqa: E402
from clipnote.contract import validate  # noqa: E402

app = FastAPI(title="clipnote-server", version="0.1.0")


class AnalyzeRequest(BaseModel):
    url: str
    profile: str = "generic"
    language: str = "ko"
    max_guides: int = Field(default=5, ge=0, le=20)
    model: str = "gemini-flash-lite-latest"
    duration: int | None = Field(
        default=None, ge=1,
        description="영상 길이(초). 플레이어를 가진 클라이언트가 넘기면 서버는 유튜브에 접속하지 않는다.")


class DocumentRequest(BaseModel):
    video_id: str
    analysis: dict
    image_refs: dict[str, str] = Field(
        default_factory=dict,
        description="클라이언트가 캡처·호스팅한 이미지 참조 (guide_id -> URL/경로)")


class ReportRequest(BaseModel):
    """One-tap issue report from clients — failure-case corpus for prompt iteration."""
    url: str
    video_id: str
    reason: Literal["candidates", "guide_text", "steps", "other"]
    note: str = Field(default="", max_length=2000)
    profile: str = "generic"
    language: str = "ko"
    analysis: dict
    picks: dict[str, str] = Field(default_factory=dict)
    client: str = ""


def require_key(x_gemini_key: str | None) -> str:
    if not x_gemini_key:
        raise HTTPException(status_code=401, detail="X-Gemini-Key 헤더가 필요합니다.")
    return x_gemini_key


@app.get("/healthz")
def healthz():
    import clipnote as core
    return {"status": "ok", "core": str(Path(core.__file__).parent)}


@app.post("/v1/analyze")
def analyze_video(req: AnalyzeRequest, x_gemini_key: str | None = Header(default=None)):
    key = require_key(x_gemini_key)
    try:
        vid = video_id(req.url)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    duration = req.duration
    if duration is None:
        try:
            duration = core_analyze.fetch_duration(req.url)
        except SystemExit:
            raise HTTPException(
                status_code=422,
                detail="영상 길이를 조회하지 못했습니다. duration을 함께 보내주세요.")

    prompt = core_analyze.load_prompt(
        req.profile, core_analyze.hms(duration), req.language, req.max_guides)
    schema = core_analyze.load_schema(req.profile)
    try:
        data = core_analyze.normalize(core_analyze.call_gemini(
            req.url, prompt, req.model, key, schema))
    except core_analyze.RateLimitError as error:
        raise HTTPException(status_code=429, detail=str(error)[-500:])
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)[-500:])

    data["_duration"] = duration
    data["_profile"] = req.profile
    data["_output_language"] = req.language
    data["_max_visual_guides"] = req.max_guides
    data["_model"] = req.model
    errors, warnings = validate(data)
    if errors:
        raise HTTPException(
            status_code=502, detail={"message": "분석 결과 계약 위반", "errors": errors})
    return {"video_id": vid, "analysis": data, "warnings": warnings}


@app.post("/v1/documents")
def build_document(req: DocumentRequest):
    profile = req.analysis.get("_profile")
    if not profile:
        raise HTTPException(status_code=422, detail="analysis._profile 이 없습니다.")
    try:
        template = core_render.load_template(profile)
    except SystemExit:
        raise HTTPException(status_code=422, detail=f"알 수 없는 프로파일: {profile}")
    body = template.split("\n---\n", 1)[1] if "\n---\n" in template else template

    with tempfile.TemporaryDirectory() as temp:
        context = core_render.build_context(
            req.video_id, req.analysis, picks={},
            source_frames=Path(temp) / "no-frames",
            images_dir=Path(temp),
            image_refs=req.image_refs)
    markdown = core_render.render(body, context).strip() + "\n"

    guides = [guide for step in context["steps"] for guide in step["visual_guides"]]
    return {
        "markdown": markdown,
        "screenshots": sum(1 for guide in guides if guide["has_screenshot"]),
        "link_fallbacks": sum(1 for guide in guides if not guide["has_screenshot"]),
    }


def _github_issue_payload(entry: dict) -> dict:
    title = (f"[report:{entry['reason']}] "
             f"{entry['analysis'].get('title', '(제목 없음)')} ({entry['video_id']})")
    body = (
        f"- **사유**: {entry['reason']}\n"
        f"- **영상**: {entry['url']}\n"
        f"- **프로파일/언어**: {entry['profile']} / {entry['language']}\n"
        f"- **client**: {entry['client']}\n"
        f"- **received_at**: {entry['received_at']}\n\n"
        f"**메모**\n\n{entry['note'] or '(없음)'}\n\n"
        "<details><summary>analysis JSON</summary>\n\n```json\n"
        + json.dumps(entry["analysis"], ensure_ascii=False, indent=2)
        + "\n```\n\n</details>\n\n"
        "<details><summary>picks</summary>\n\n```json\n"
        + json.dumps(entry["picks"], ensure_ascii=False, indent=2)
        + "\n```\n\n</details>"
    )
    return {"title": title, "body": body,
            "labels": ["report", f"report:{entry['reason']}"]}


def _post_issue_with_token(repo: str, token: str, payload: dict) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return "ok" if 200 <= response.status < 300 else "failed"
    except Exception:
        return "failed"


def _create_github_issue(entry: dict) -> str:
    """Optional bridge after the JSONL write — never fails the report.

    Prefers GITHUB_TOKEN (works on hosted deploys without gh CLI), falls back
    to the local `gh` CLI, else "skipped". Opt-in via CLIPNOTE_REPORTS_REPO.
    Returns "ok" | "skipped" | "failed".
    """
    repo = os.environ.get("CLIPNOTE_REPORTS_REPO")
    if not repo:
        return "skipped"
    payload = _github_issue_payload(entry)
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return _post_issue_with_token(repo, token, payload)
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues", "--input", "-"],
            input=json.dumps(payload).encode(),
            capture_output=True, timeout=15)
        return "ok" if result.returncode == 0 else "failed"
    except (OSError, subprocess.TimeoutExpired):
        return "failed"


@app.post("/v1/reports")
def submit_report(req: ReportRequest):
    """Append the report as one JSONL line. The only stateful endpoint —
    an explicit exception to the stateless design, for the feedback loop."""
    reports_dir = Path(os.environ.get("CLIPNOTE_REPORTS", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    entry = req.model_dump()
    entry["received_at"] = datetime.now(timezone.utc).isoformat()
    with (reports_dir / "reports.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"status": "ok", "github": _create_github_issue(entry)}


def main():
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8787")))


if __name__ == "__main__":
    main()
