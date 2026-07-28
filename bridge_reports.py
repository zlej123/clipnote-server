#!/usr/bin/env python3
"""reports.jsonl → GitHub 이슈 배치 브리지.

공개 신고 엔드포인트에서 이슈 생성을 분리했다 (외부 리뷰 #1): 요청 경로가 토큰 권한을
직접 구동하면 스팸 한 번에 이슈 폭탄이 된다. 이 스크립트를 운영자가 주기적으로(cron 등)
돌리면, 아직 브리지되지 않은 신고만 이슈로 만든다.

사용:
    STEPKEEPER_REPORTS_REPO=owner/repo python bridge_reports.py
    # 토큰: GITHUB_TOKEN 있으면 urllib, 없으면 gh CLI

상태는 reports/.bridged(처리한 줄 수)에 남는다. 실패하면 그 줄에서 멈추고
다음 실행 때 재시도한다 (이슈 중복 방지).
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


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
    to the local `gh` CLI, else "skipped". Opt-in via STEPKEEPER_REPORTS_REPO.
    Returns "ok" | "skipped" | "failed".
    """
    repo = os.environ.get("STEPKEEPER_REPORTS_REPO")
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


def main() -> int:
    repo = os.environ.get("STEPKEEPER_REPORTS_REPO")
    if not repo:
        sys.exit("STEPKEEPER_REPORTS_REPO 환경변수가 없습니다 (owner/repo).")
    reports_dir = Path(os.environ.get("STEPKEEPER_REPORTS", "reports"))
    source = reports_dir / "reports.jsonl"
    if not source.exists():
        print("신고 없음:", source)
        return 0
    state_path = reports_dir / ".bridged"
    done = int(state_path.read_text()) if state_path.exists() else 0
    lines = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    created = 0
    for line in lines[done:]:
        entry = json.loads(line)
        status = _create_github_issue(entry)
        if status != "ok":
            print(f"중단: {status} (줄 {done + created + 1}) — 다음 실행에서 재시도")
            break
        created += 1
        state_path.write_text(str(done + created))
    print(f"이슈 생성 {created}건 (누적 {done + created}/{len(lines)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
