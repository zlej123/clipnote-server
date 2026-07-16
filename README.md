# clipnote-server

[clipnote](https://github.com/zlej123/clipnote) 코어를 감싸는 얇은 REST API. **서버는 "두뇌"(영상→분석 JSON)만 담당**하고, 프레임 캡처는 클라이언트(애플 앱: WKWebView, 크롬 확장: canvas)가 자기 화면에서 수행합니다. 그래서 서버에는 ffmpeg가 필요 없고, 상태를 저장하지 않으며, 유튜브에 접속하지도 않습니다(클라이언트가 `duration`을 넘길 때).

## 왜 얇은 서버인가

| 역할 | 담당 |
|------|------|
| 영상 분석 (Gemini) | 서버 `/v1/analyze` |
| 프레임 캡처 | 클라이언트 (플레이어 화면에서) |
| 문서 조립 | 서버 `/v1/documents` (클라이언트 이미지 참조 삽입) |
| 비용 | 사용자 본인 Gemini 키 (`X-Gemini-Key` 패스스루, BYOK) |

## API

### `POST /v1/analyze`
```
header: X-Gemini-Key: <사용자 Gemini API 키>   # 필수
body: {
  "url": "https://www.youtube.com/watch?v=...",
  "profile": "generic",        # generic | recipe
  "language": "ko",            # 출력 언어 (BCP-47)
  "max_guides": 5,
  "duration": 416              # 초. 플레이어를 가진 클라이언트가 넘기면 서버는 유튜브 무접촉
}
→ 200 { "video_id", "analysis": { steps[], visual_guides[], ... }, "warnings[] }
→ 401 키 없음 | 422 잘못된 URL/프로파일 | 429 Gemini 한도 | 502 모델 오류/계약 위반
```

### `POST /v1/documents`
```
body: {
  "video_id": "...",
  "analysis": { /v1/analyze 응답의 analysis 그대로 },
  "image_refs": { "vg-1": "https://.../frame.jpg" }   # 클라이언트가 캡처한 이미지 (선택)
}
→ 200 { "markdown", "screenshots", "link_fallbacks" }
```
`image_refs`에 없는 가이드는 유튜브 타임스탬프 링크로 폴백합니다.

## 실행

```bash
pip install -r requirements.txt
# clipnote 코어 위치 (기본: ../clipnote)
export CLIPNOTE_PATH=/path/to/clipnote
python app.py                    # http://127.0.0.1:8787
```

Docker:
```bash
docker build -t clipnote-server .
docker run -p 8787:8787 clipnote-server
```

## 테스트

```bash
python -m unittest discover -s tests   # Gemini 스텁, 네트워크 불필요
```

## 요구사항

- Python 3.10+
- 실제 분석 호출 시 클라이언트가 유효한 Gemini API 키를 보내야 함 (서버에 키 저장 없음)

## 라이선스

MIT
