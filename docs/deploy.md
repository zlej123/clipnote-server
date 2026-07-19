# Deploying the report collector (Cloud Run)

The app's one-tap reports need a hosted collector so end users configure nothing.
This is the same clipnote-server — deploy it once, then point the app's
`ReportCollector.defaultURL` at it.

## Deploy

    gcloud run deploy clipnote-reports --source . --region asia-northeast3 \
      --allow-unauthenticated \
      --set-env-vars CLIPNOTE_REPORTS_REPO=zlej123/clipnote-reports,GITHUB_TOKEN=<fine-grained PAT>

- Token: fine-grained PAT, **Issues Read/Write on the reports repo only**.
- `CLIPNOTE_REPORTS` (JSONL dir) is ephemeral on Cloud Run — GitHub issues are
  the durable record. Mount a bucket later if you want the JSONL corpus too.
- `/v1/analyze`·`/v1/documents` also work on this deployment (BYOK passthrough,
  the server still pays for nothing) — optional for users who prefer server mode.

## After deploying

1. `curl -s https://<service-url>/healthz` → `{"status": "ok", ...}`
2. clipnote-apple의 `Sources/Models/ReportCollector.swift` `defaultURL`을
   서비스 URL로 교체하고 릴리스.
