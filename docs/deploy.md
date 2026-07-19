# 신고 수집기 배포 가이드 (Cloud Run)

앱의 원탭 신고(🚩)를 **일반 사용자가 아무 설정 없이** 쓰게 하려면 이 서버를 한 번 배포해서
그 주소를 앱에 내장해야 한다. 배포하는 것은 이 저장소(clipnote-server) 그대로이고,
받은 신고는 JSONL로 저장되면서 비공개 저장소 이슈로도 자동 등록된다.

배포 후에도 분석은 앱이 Gemini를 직접 호출한다(서버리스 기본). 이 배포는 **신고 수집 용도**이며,
서버 모드를 선호하는 사용자를 위한 `/v1/analyze`·`/v1/documents`도 함께 열린다(BYOK 패스스루라 추론 비용은 여전히 호출자 부담).

---

## 0. 준비물 (최초 1회)

**① GCP 프로젝트 + 결제 계정**
[console.cloud.google.com](https://console.cloud.google.com)에서 프로젝트를 만들고 결제 계정을 연결한다.
Cloud Run은 월 200만 요청까지 무료라 이 용도(신고 몇 건)로는 사실상 0원이지만, **결제 카드 등록은 필요**하다.

**② gcloud CLI 설치·로그인**

```bash
brew install --cask google-cloud-sdk
gcloud auth login                      # 브라우저에서 구글 계정 승인
gcloud config set project <프로젝트ID>   # 예: clipnote-470012
gcloud services enable run.googleapis.com cloudbuild.googleapis.com
```

**③ GitHub 토큰 발급** (신고를 이슈로 등록하는 데 사용)
[github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens) →
**Fine-grained token** 생성:

- Repository access: **Only select repositories** → `zlej123/clipnote-reports`
- Permissions → Repository permissions → **Issues: Read and write** (이것만. 다른 권한 불필요)
- 만료일: 1년 권장 (만료 시 4절의 갱신 절차)

생성된 `github_pat_...` 문자열을 복사해 둔다. **이 값은 커밋하지 말 것** — 배포 명령에만 쓴다.

---

## 1. 배포

저장소 루트(`clipnote-server/`)에서:

```bash
gcloud run deploy clipnote-reports \
  --source . \
  --region asia-northeast3 \
  --allow-unauthenticated \
  --set-env-vars CLIPNOTE_REPORTS_REPO=zlej123/clipnote-reports \
  --set-env-vars GITHUB_TOKEN=<복사한 토큰>
```

- `--source .`이면 Dockerfile로 자동 빌드된다(별도 이미지 준비 불필요). 첫 배포는 3~5분.
- `--allow-unauthenticated`: 앱이 인증 없이 신고를 보낼 수 있어야 하므로 공개 접근. 신고 엔드포인트는 저장만 하고 아무것도 반환하지 않는다.
- `asia-northeast3`은 서울 리전. 다른 곳도 무방.
- 완료되면 `Service URL: https://clipnote-reports-xxxxx.a.run.app` 형태의 주소가 출력된다 — **다음 단계에서 쓴다.**

---

## 2. 동작 확인

```bash
SERVICE=https://clipnote-reports-xxxxx.a.run.app   # 1절에서 받은 주소

curl -s $SERVICE/healthz
# → {"status":"ok","core":"/usr/local/lib/python3.12/site-packages/clipnote"}

curl -s -X POST $SERVICE/v1/reports \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://youtu.be/TEST","video_id":"TEST","reason":"other",
       "note":"배포 확인","profile":"generic","language":"ko",
       "analysis":{"title":"배포 확인"},"picks":{},"client":"deploy/test"}'
# → {"status":"ok","github":"ok"}
```

`"github":"ok"`가 나오면 [clipnote-reports 저장소](https://github.com/zlej123/clipnote-reports/issues)에
이슈가 생성돼 있어야 한다. 확인 후 그 테스트 이슈는 닫는다.

`"github":"failed"`면 토큰 문제다 — 권한(Issues RW)과 대상 저장소 선택을 다시 확인하고,
`gcloud run services update clipnote-reports --region asia-northeast3 --set-env-vars GITHUB_TOKEN=<새 토큰>`으로 갱신한다.

---

## 3. 앱에 반영

`clipnote-apple/Sources/Models/ReportCollector.swift`의 한 줄만 바꾼다:

```swift
    static let defaultURL = "https://clipnote-reports-xxxxx.a.run.app"
```

이후 빌드부터는 사용자가 설정을 전혀 건드리지 않아도 🚩 신고가 이 주소로 전송된다.
(설정에 신고 URL을 직접 넣으면 그 값이 우선한다 — 개발자용 우회 경로.)

```bash
cd ../clipnote-apple
xcodebuild -project clipnote-apple.xcodeproj -scheme Clipnote \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test   # 회귀 확인
git add Sources/Models/ReportCollector.swift
git commit -m "chore: 신고 수집기 주소 반영"
```

---

## 4. 운영 메모

**비용**: 신고는 하루 몇 건 수준이라 Cloud Run 무료 한도 안에 머문다. 트래픽이 없으면 인스턴스가 0으로 줄어
과금되지 않는다(첫 요청 시 몇 초 콜드 스타트).

**JSONL은 휘발성**: 컨테이너 파일시스템은 재시작 시 사라지므로 `reports/reports.jsonl`은 영속 기록이 아니다.
**GitHub 이슈가 영속 기록**이다. 원본 JSONL 코퍼스까지 남기려면 Cloud Storage 볼륨을 마운트해야 한다(현재는 미구성).

**토큰 갱신**: 만료 전에 새 토큰을 발급하고
`gcloud run services update clipnote-reports --region asia-northeast3 --set-env-vars GITHUB_TOKEN=<새 토큰>`
(다른 env는 유지된다 — `--set-env-vars`는 지정한 키만 덮어쓴다).

**로그 확인**:
```bash
gcloud run services logs read clipnote-reports --region asia-northeast3 --limit 50
```

**신고 활용**: 쌓인 이슈의 영상 URL을 코어의 회귀 코퍼스(`clipnote/tests/fixtures/urls.json`)에 추가하고
프롬프트를 고친 뒤, 커밋 메시지에 `zlej123/clipnote-reports#<번호>`를 적으면 개선 이력이 연결된다.

---

## 부록: 더 간단한 대안 (카드 등록 없이)

Cloud Run의 결제 등록이 부담이면 [Render](https://render.com) 무료 플랜도 가능하다 — 웹 UI에서
GitHub 저장소를 연결하고 Docker 환경으로 선택한 뒤 환경변수(`CLIPNOTE_REPORTS_REPO`, `GITHUB_TOKEN`)만 넣으면 된다.
단점은 무료 플랜의 콜드 스타트가 길다는 것(최대 1분) — 신고는 사용자가 결과를 기다리지 않는 동작이라
체감 문제는 적지만, 앱의 신고 타임아웃(30초)에 걸릴 수 있으니 첫 신고가 실패하면 재시도가 필요할 수 있다.
