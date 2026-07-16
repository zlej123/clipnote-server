import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

import app as server
from app import app

client = TestClient(app)

STUB_ANALYSIS = {
    "title": "테스트 가이드",
    "summary": "요약",
    "category": "공예",
    "materials": [{"name": "종이", "amount": "1장"}],
    "steps": [
        {"id": 1, "summary": "접기", "detail": "종이를 접는다.",
         "t_start": "0:00", "t_end": "0:10"},
    ],
    "visual_guides": [
        {"id": "vg-1", "step_id": 1, "source_phrase": "fold it",
         "phrase": "반으로 접기", "type": "action",
         "what_to_show": "모서리가 만나는 장면",
         "best_visual_timestamp": "0:08",
         "guide_text": "두 모서리가 정확히 겹치도록 반으로 접는다.",
         "importance": 0.9},
    ],
}
URL = "https://www.youtube.com/watch?v=GC_Szxdqh2Y"


class AnalyzeEndpointTests(unittest.TestCase):
    def test_requires_gemini_key(self):
        response = client.post("/v1/analyze", json={"url": URL})
        self.assertEqual(401, response.status_code)

    def test_rejects_invalid_url(self):
        response = client.post("/v1/analyze", json={"url": "https://example.com/x"},
                               headers={"X-Gemini-Key": "k"})
        self.assertEqual(422, response.status_code)

    def test_analyze_returns_validated_analysis(self):
        with patch.object(server.core_analyze, "call_gemini",
                          return_value=dict(STUB_ANALYSIS)) as gemini:
            response = client.post(
                "/v1/analyze",
                json={"url": URL, "duration": 20, "language": "ko"},
                headers={"X-Gemini-Key": "user-key"})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("GC_Szxdqh2Y", body["video_id"])
        analysis = body["analysis"]
        self.assertEqual(8, analysis["visual_guides"][0]["best_visual_timestamp"])
        self.assertEqual(20, analysis["_duration"])
        self.assertEqual("ko", analysis["_output_language"])
        # duration was provided -> server never touched YouTube
        self.assertEqual("user-key", gemini.call_args.args[3])

    def test_rate_limit_maps_to_429(self):
        with patch.object(server.core_analyze, "call_gemini",
                          side_effect=server.core_analyze.RateLimitError("quota")):
            response = client.post(
                "/v1/analyze", json={"url": URL, "duration": 20},
                headers={"X-Gemini-Key": "k"})
        self.assertEqual(429, response.status_code)


class DocumentEndpointTests(unittest.TestCase):
    def analysis_seconds(self):
        with patch.object(server.core_analyze, "call_gemini",
                          return_value=dict(STUB_ANALYSIS)):
            return client.post(
                "/v1/analyze", json={"url": URL, "duration": 20},
                headers={"X-Gemini-Key": "k"}).json()["analysis"]

    def test_document_link_fallback(self):
        response = client.post("/v1/documents", json={
            "video_id": "GC_Szxdqh2Y", "analysis": self.analysis_seconds()})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(0, body["screenshots"])
        self.assertEqual(1, body["link_fallbacks"])
        self.assertIn("https://youtu.be/GC_Szxdqh2Y?t=8", body["markdown"])

    def test_document_embeds_client_image_refs(self):
        response = client.post("/v1/documents", json={
            "video_id": "GC_Szxdqh2Y",
            "analysis": self.analysis_seconds(),
            "image_refs": {"vg-1": "https://cdn.example.com/vg-1.jpg"}})
        body = response.json()
        self.assertEqual(1, body["screenshots"])
        self.assertIn("https://cdn.example.com/vg-1.jpg", body["markdown"])


if __name__ == "__main__":
    unittest.main()
