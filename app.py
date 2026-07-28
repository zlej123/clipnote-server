#!/usr/bin/env python3
"""stepkeeper-server: thin REST wrapper around the stepkeeper core.

Design: the server is the shared "brain" only.
- POST /v1/analyze   — video URL -> validated analysis JSON (steps + visual_guides).
- POST /v1/documents — analysis (+ optional client-captured image refs) -> markdown.
- POST /v1/reports    — one-tap issue report (JSONL append; the stateless exception).
- Frame capture is the client's job (Apple app: WKWebView, extension: canvas),
  so the server needs no ffmpeg and stays stateless.
- BYOK: the caller sends their own Gemini key in `X-Gemini-Key`; the server
  never pays for inference and stores nothing.

The stepkeeper core is used as an installed package (`pip install stepkeeper`),
with a repo fallback via STEPKEEPER_PATH (default: ../stepkeeper).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

try:
    import stepkeeper  # noqa: F401  (pip-installed package)
except ImportError:
    STEPKEEPER_PATH = Path(os.environ.get(
        "STEPKEEPER_PATH", Path(__file__).parent.parent / "stepkeeper")).resolve()
    if not (STEPKEEPER_PATH / "src" / "stepkeeper" / "analyze.py").exists():
        raise RuntimeError(
            f"stepkeeper package not importable and repo not at {STEPKEEPER_PATH}; "
            "pip install stepkeeper or set STEPKEEPER_PATH")
    sys.path.insert(0, str(STEPKEEPER_PATH / "src"))

from fastapi import FastAPI, Header, HTTPException, Request  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from stepkeeper import analyze as core_analyze  # noqa: E402
from stepkeeper import common as core_common  # noqa: E402
from stepkeeper import render as core_render  # noqa: E402
from stepkeeper.common import video_id  # noqa: E402
from stepkeeper.contract import validate  # noqa: E402

app = FastAPI(title="stepkeeper-server", version="0.1.0")


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
    """One-tap issue report from clients — failure-case corpus for prompt iteration.

    공개 배포를 전제로 모든 필드에 상한을 둔다 (리뷰 #1) — 무제한 analysis/picks는
    디스크·대역폭 소진 경로였다.
    """
    url: str = Field(max_length=500)
    video_id: str = Field(max_length=20)
    reason: Literal["candidates", "guide_text", "steps", "other"]
    note: str = Field(default="", max_length=2000)
    profile: str = Field(default="generic", max_length=40)
    language: str = Field(default="ko", max_length=40)
    analysis: dict
    picks: dict[str, str] = Field(default_factory=dict)
    client: str = Field(default="", max_length=100)


# 신고 엔드포인트 보호 장치 (모두 인메모리 — 서버 재시작 시 초기화되는 best-effort.
# reports 저장과 같은 "stateless 예외" 항목이며, 완전한 보호가 아니라 남용 비용을 올리는 장치다)
REPORT_MAX_ANALYSIS_BYTES = 200_000
REPORT_MAX_PICKS = 100
REPORT_RATE_LIMIT = 10          # IP당 시간당
REPORT_RATE_WINDOW = 3600.0
REPORT_DEDUP_WINDOW = 600.0
_report_hits: dict[str, list] = {}
_report_seen: dict[str, float] = {}


def _client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_key(x_gemini_key: str | None) -> str:
    if not x_gemini_key:
        raise HTTPException(status_code=401, detail="X-Gemini-Key 헤더가 필요합니다.")
    return x_gemini_key


@app.get("/healthz")
def healthz():
    import stepkeeper as core
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

    try:
        prompt = core_analyze.load_prompt(
            req.profile, core_analyze.hms(duration), req.language, req.max_guides)
        schema = core_analyze.load_schema(req.profile)
    except core_common.UnknownProfileError as error:
        raise HTTPException(status_code=422, detail=str(error))
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
        # 문서 뼈대는 분석의 출력 언어를 따른다 — 이 인자를 빠뜨리면 ko/ja 분석도 영어 뼈대가 된다
        template = core_render.load_template(
            profile, req.analysis.get("_output_language") or "")
    except core_common.UnknownProfileError as error:
        raise HTTPException(status_code=422, detail=str(error))
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


@app.post("/v1/reports")
def submit_report(req: ReportRequest, request: Request,
                  x_report_token: str | None = Header(default=None)):
    """Append the report as one JSONL line. The only stateful endpoint —
    an explicit exception to the stateless design, for the feedback loop.

    GitHub 이슈 생성은 이 경로에서 하지 않는다 (리뷰 #1: 공개 요청이 토큰 권한을 직접
    구동하면 안 된다) — bridge_reports.py를 별도로 돌려 JSONL에서 배치로 만든다.
    """
    expected = os.environ.get("STEPKEEPER_REPORTS_TOKEN")
    if expected and x_report_token != expected:
        raise HTTPException(status_code=401, detail="X-Report-Token이 올바르지 않습니다.")

    now = time.monotonic()
    ip = _client_ip(request)
    hits = [t for t in _report_hits.get(ip, []) if now - t < REPORT_RATE_WINDOW]
    if len(hits) >= REPORT_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="신고 한도 초과 — 잠시 후 다시 시도하세요.")
    hits.append(now)
    _report_hits[ip] = hits

    if len(req.picks) > REPORT_MAX_PICKS:
        raise HTTPException(status_code=413, detail="picks가 너무 많습니다.")
    analysis_bytes = len(json.dumps(req.analysis, ensure_ascii=False).encode())
    if analysis_bytes > REPORT_MAX_ANALYSIS_BYTES:
        raise HTTPException(status_code=413, detail="analysis가 너무 큽니다 (200KB 제한).")

    digest = hashlib.sha256(
        f"{req.video_id}|{req.reason}|{req.note}|{req.client}".encode()).hexdigest()
    for key, ts in list(_report_seen.items()):
        if now - ts > REPORT_DEDUP_WINDOW:
            del _report_seen[key]
    if digest in _report_seen:
        return {"status": "duplicate"}
    _report_seen[digest] = now

    reports_dir = Path(os.environ.get("STEPKEEPER_REPORTS", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    entry = req.model_dump()
    entry["received_at"] = datetime.now(timezone.utc).isoformat()
    with (reports_dir / "reports.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"status": "ok"}


def main():
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8787")))


if __name__ == "__main__":
    main()
