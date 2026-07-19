import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient


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
        os.environ["CLIPNOTE_REPORTS"] = self.tmp.name
        import app  # noqa: WPS433 — env 설정 후 임포트
        self.client = TestClient(app.app)

    def tearDown(self):
        os.environ.pop("CLIPNOTE_REPORTS", None)
        self.tmp.cleanup()

    def test_appends_jsonl_with_received_at(self):
        response = self.client.post("/v1/reports", json=make_payload())
        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())
        path = os.path.join(self.tmp.name, "reports.jsonl")
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(1, len(lines))
        entry = json.loads(lines[0])
        self.assertEqual("GziiD4XqCpc", entry["video_id"])
        self.assertEqual("gemini-flash-lite-latest", entry["analysis"]["_model"])
        self.assertIn("received_at", entry)

    def test_two_reports_two_lines(self):
        self.client.post("/v1/reports", json=make_payload())
        self.client.post("/v1/reports", json=make_payload(reason="other"))
        path = os.path.join(self.tmp.name, "reports.jsonl")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(2, len(f.readlines()))

    def test_rejects_bad_reason_and_long_note(self):
        self.assertEqual(
            422, self.client.post("/v1/reports", json=make_payload(reason="nonsense")).status_code)
        self.assertEqual(
            422, self.client.post("/v1/reports", json=make_payload(note="x" * 2001)).status_code)


if __name__ == "__main__":
    unittest.main()
