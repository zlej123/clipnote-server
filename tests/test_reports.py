import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

import bridge_reports  # noqa: E402


def make_payload(**overrides):
    payload = {
        "url": "https://m.youtube.com/watch?v=GziiD4XqCpc",
        "video_id": "GziiD4XqCpc",
        "reason": "candidates",
        "note": "후보 3장이 전부 인트로 화면",
        "profile": "recipe",
        "language": "ko",
        "analysis": {"title": "t", "_model": "gemini-flash-lite-latest"},
        "picks": {"vg-1": "none"},
        "client": "apple/0.1.0",
    }
    payload.update(overrides)
    return payload


class ReportsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STEPKEEPER_REPORTS"] = self.tmp.name
        os.environ.pop("STEPKEEPER_REPORTS_TOKEN", None)
        import app  # noqa: WPS433 — env 설정 후 임포트
        self.app = app
        app._report_hits.clear()   # 인메모리 보호 장치 초기화 (테스트 간 간섭 차단)
        app._report_seen.clear()
        self.client = TestClient(app.app)

    def tearDown(self):
        os.environ.pop("STEPKEEPER_REPORTS", None)
        os.environ.pop("STEPKEEPER_REPORTS_TOKEN", None)
        self.tmp.cleanup()

    def lines(self):
        path = Path(self.tmp.name) / "reports.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_appends_jsonl_with_received_at(self):
        response = self.client.post("/v1/reports", json=make_payload())
        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())   # github 필드 없음 — 브리지는 오프라인
        entries = self.lines()
        self.assertEqual(1, len(entries))
        self.assertIn("received_at", entries[0])
        self.assertEqual("GziiD4XqCpc", entries[0]["video_id"])

    def test_two_distinct_reports_two_lines(self):
        self.client.post("/v1/reports", json=make_payload())
        self.client.post("/v1/reports", json=make_payload(note="다른 내용"))
        self.assertEqual(2, len(self.lines()))

    def test_rejects_bad_reason_and_long_note(self):
        self.assertEqual(422, self.client.post(
            "/v1/reports", json=make_payload(reason="nope")).status_code)
        self.assertEqual(422, self.client.post(
            "/v1/reports", json=make_payload(note="x" * 2001)).status_code)

    # ---- 하드닝 (외부 리뷰 #1) ------------------------------------------------
    def test_duplicate_within_window_is_not_written_twice(self):
        first = self.client.post("/v1/reports", json=make_payload())
        second = self.client.post("/v1/reports", json=make_payload())
        self.assertEqual({"status": "ok"}, first.json())
        self.assertEqual({"status": "duplicate"}, second.json())
        self.assertEqual(1, len(self.lines()))

    def test_rate_limit_returns_429(self):
        for i in range(self.app.REPORT_RATE_LIMIT):
            response = self.client.post("/v1/reports", json=make_payload(note=f"n{i}"))
            self.assertEqual(200, response.status_code)
        response = self.client.post("/v1/reports", json=make_payload(note="한도 초과"))
        self.assertEqual(429, response.status_code)
        self.assertEqual(self.app.REPORT_RATE_LIMIT, len(self.lines()))

    def test_oversized_analysis_is_413(self):
        big = {"blob": "가" * self.app.REPORT_MAX_ANALYSIS_BYTES}
        response = self.client.post("/v1/reports", json=make_payload(analysis=big))
        self.assertEqual(413, response.status_code)
        self.assertEqual(0, len(self.lines()))

    def test_too_many_picks_is_413(self):
        picks = {f"vg-{i}": "center" for i in range(self.app.REPORT_MAX_PICKS + 1)}
        response = self.client.post("/v1/reports", json=make_payload(picks=picks))
        self.assertEqual(413, response.status_code)

    def test_field_length_caps(self):
        self.assertEqual(422, self.client.post(
            "/v1/reports", json=make_payload(url="https://youtu.be/" + "x" * 500)).status_code)
        self.assertEqual(422, self.client.post(
            "/v1/reports", json=make_payload(client="c" * 101)).status_code)

    def test_token_auth_when_configured(self):
        os.environ["STEPKEEPER_REPORTS_TOKEN"] = "secret-1"
        self.assertEqual(401, self.client.post(
            "/v1/reports", json=make_payload()).status_code)
        self.assertEqual(401, self.client.post(
            "/v1/reports", json=make_payload(),
            headers={"X-Report-Token": "wrong"}).status_code)
        self.assertEqual(200, self.client.post(
            "/v1/reports", json=make_payload(),
            headers={"X-Report-Token": "secret-1"}).status_code)


class BridgeCLITests(unittest.TestCase):
    """GitHub 이슈 브리지는 요청 경로가 아니라 오프라인 배치다 (외부 리뷰 #1)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["STEPKEEPER_REPORTS"] = self.tmp.name
        os.environ["STEPKEEPER_REPORTS_REPO"] = "zlej123/stepkeeper-reports"
        os.environ.pop("GITHUB_TOKEN", None)
        self.bridge = bridge_reports
        source = Path(self.tmp.name) / "reports.jsonl"
        with source.open("w", encoding="utf-8") as f:
            for i in range(3):
                f.write(json.dumps(dict(make_payload(note=f"n{i}"),
                                        received_at="2026-07-28T00:00:00Z")) + "\n")

    def tearDown(self):
        for key in ("STEPKEEPER_REPORTS", "STEPKEEPER_REPORTS_REPO", "GITHUB_TOKEN"):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def state(self):
        path = Path(self.tmp.name) / ".bridged"
        return int(path.read_text()) if path.exists() else 0

    @patch("bridge_reports.subprocess")
    def test_bridges_new_lines_and_records_state(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        self.bridge.main()
        self.assertEqual(3, self.state())
        self.assertEqual(3, mock_subprocess.run.call_count)
        # 재실행하면 이미 처리한 줄은 건너뛴다
        mock_subprocess.run.reset_mock()
        self.bridge.main()
        self.assertEqual(0, mock_subprocess.run.call_count)

    @patch("bridge_reports.subprocess")
    def test_failure_stops_and_resumes_next_run(self, mock_subprocess):
        mock_subprocess.run.side_effect = [
            MagicMock(returncode=0), MagicMock(returncode=1)]
        self.bridge.main()
        self.assertEqual(1, self.state())   # 실패 지점에서 멈춤 — 이슈 중복 방지
        mock_subprocess.run.side_effect = None
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        self.bridge.main()
        self.assertEqual(3, self.state())

    def test_token_path_posts_via_urllib(self):
        os.environ["GITHUB_TOKEN"] = "tok"
        captured = {}

        class FakeResponse:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *args): return False

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.headers.get("Authorization")
            return FakeResponse()

        with patch("bridge_reports.urllib.request.urlopen",
                   side_effect=fake_urlopen):
            self.bridge.main()
        self.assertEqual(3, self.state())
        self.assertEqual(
            "https://api.github.com/repos/zlej123/stepkeeper-reports/issues",
            captured["url"])
        self.assertEqual("Bearer tok", captured["auth"])


if __name__ == "__main__":
    unittest.main()
