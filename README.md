# clipnote-server

A thin REST API around the [clipnote](https://github.com/zlej123/clipnote) core. The server does analysis only (video → JSON); frame capture happens on the client, from its own player. The server needs no ffmpeg, stores nothing beyond opt-in failure reports, and — when the client passes `duration` — never contacts YouTube.

| Concern | Owner |
|---------|-------|
| Video analysis (Gemini) | server `/v1/analyze` |
| Frame capture | client (Apple app: WKWebView, extension: canvas) |
| Document assembly | server `/v1/documents`, embedding client image refs |
| Failure reports | server `/v1/reports` (JSONL, opt-in one-tap) |
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

### `POST /v1/reports`
```
body: {
  "url": "https://www.youtube.com/watch?v=...",
  "video_id": "...",
  "reason": "candidates",       # candidates | guide_text | steps | other
  "note": "",                   # optional, <= 2000 chars
  "profile": "generic",
  "language": "ko",
  "analysis": { the analysis object being reported on },
  "picks": { "vg-1": "none" },  # optional, client-side pick state
  "client": "apple/0.1.0"
}
→ 200 { "status": "ok" }
→ 422 invalid reason / note too long
```
Appends one JSONL line (with a server-added `received_at`, UTC ISO8601) to `${CLIPNOTE_REPORTS:-reports}/reports.jsonl`. **This is the only endpoint that stores anything — an explicit exception to the stateless design** (`CLIPNOTE_REPORTS`, default `reports/`), kept for the one-tap failure-case feedback loop.

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
