# clipnote-server

A thin REST API around the [clipnote](https://github.com/zlej123/clipnote) core. The server does analysis only (video → JSON); frame capture happens on the client, from its own player. The server needs no ffmpeg, stores nothing, and — when the client passes `duration` — never contacts YouTube.

| Concern | Owner |
|---------|-------|
| Video analysis (Gemini) | server `/v1/analyze` |
| Frame capture | client (Apple app: WKWebView, extension: canvas) |
| Document assembly | server `/v1/documents`, embedding client image refs |
| Cost | the caller's own Gemini key (`X-Gemini-Key` passthrough) |

## API

### `POST /v1/analyze`
```
header: X-Gemini-Key: <caller's Gemini API key>   # required
body: {
  "url": "https://www.youtube.com/watch?v=...",
  "profile": "generic",        # generic | recipe
  "language": "ko",            # output language (BCP-47)
  "max_guides": 5,
  "duration": 416              # seconds; when present, the server never touches YouTube
}
→ 200 { "video_id", "analysis": { steps[], visual_guides[], ... }, "warnings[] }
→ 401 missing key | 422 bad URL/profile | 429 Gemini rate limit | 502 model error / contract violation
```

### `POST /v1/documents`
```
body: {
  "video_id": "...",
  "analysis": { the analysis object from /v1/analyze },
  "image_refs": { "vg-1": "https://.../frame.jpg" }   # client-captured images, optional
}
→ 200 { "markdown", "screenshots", "link_fallbacks" }
```
Guides without an `image_refs` entry fall back to YouTube timestamp links.

## Run

```bash
pip install -r requirements.txt
pip install "git+https://github.com/zlej123/clipnote"   # or: pip install -e ../clipnote
python app.py                             # http://127.0.0.1:8787
```

Docker:
```bash
docker build -t clipnote-server .
docker run -p 8787:8787 clipnote-server
```

## Tests

```bash
python -m unittest discover -s tests   # Gemini stubbed, no network needed
```

Requires Python 3.10+. Callers must send their own Gemini API key; the server stores none.

## License

MIT
