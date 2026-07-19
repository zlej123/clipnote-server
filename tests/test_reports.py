import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        # NOTE: was `self.assertEqual({"status": "ok"}, response.json())` — loosened to
        # key-wise assertions because the response now also carries a `github` field
        # (see GithubIssueBridgeTests). Verification intent (status ok) is unchanged.
        body = response.json()
        self.assertEqual("ok", body["status"])
        self.assertEqual("skipped", body["github"])  # CLIPNOTE_REPORTS_REPO unset here
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


class GithubIssueBridgeTests(unittest.TestCase):
    """Optional bridge: JSONL append still happens; issue creation is opt-in
    via CLIPNOTE_REPORTS_REPO and must never turn a successful report into a
    non-200 response."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["CLIPNOTE_REPORTS"] = self.tmp.name
        import app  # noqa: WPS433 — env 설정 후 임포트
        self.client = TestClient(app.app)

    def tearDown(self):
        os.environ.pop("CLIPNOTE_REPORTS", None)
        os.environ.pop("CLIPNOTE_REPORTS_REPO", None)
        self.tmp.cleanup()

    @patch("app.subprocess.run")
    def test_creates_issue_when_repo_configured(self, mock_run):
        os.environ["CLIPNOTE_REPORTS_REPO"] = "zlej123/clipnote-reports"
        mock_run.return_value = MagicMock(returncode=0)

        response = self.client.post("/v1/reports", json=make_payload())

        self.assertEqual(200, response.status_code)
        self.assertEqual("ok", response.json()["github"])
        self.assertEqual(1, mock_run.call_count)
        _, kwargs = mock_run.call_args
        payload = json.loads(kwargs["input"])
        self.assertTrue(payload["title"].startswith("[report:candidates]"))
        self.assertIn("report:candidates", payload["labels"])

    @patch("app.subprocess.run")
    def test_skips_issue_when_repo_not_configured(self, mock_run):
        os.environ.pop("CLIPNOTE_REPORTS_REPO", None)

        response = self.client.post("/v1/reports", json=make_payload())

        self.assertEqual(200, response.status_code)
        self.assertEqual("skipped", response.json()["github"])
        self.assertEqual(0, mock_run.call_count)

    @patch("app.subprocess.run")
    def test_failed_issue_creation_keeps_200_and_jsonl(self, mock_run):
        os.environ["CLIPNOTE_REPORTS_REPO"] = "zlej123/clipnote-reports"
        mock_run.return_value = MagicMock(returncode=1)

        response = self.client.post("/v1/reports", json=make_payload())

        self.assertEqual(200, response.status_code)
        self.assertEqual("failed", response.json()["github"])
        path = os.path.join(self.tmp.name, "reports.jsonl")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(1, len(f.readlines()))

    @patch("app.subprocess.run")
    def test_issue_creation_timeout_keeps_200(self, mock_run):
        os.environ["CLIPNOTE_REPORTS_REPO"] = "zlej123/clipnote-reports"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=15)

        response = self.client.post("/v1/reports", json=make_payload())

        self.assertEqual(200, response.status_code)
        self.assertEqual("failed", response.json()["github"])


if __name__ == "__main__":
    unittest.main()
