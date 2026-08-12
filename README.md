# 동천한식뷔페 메뉴 → Teams 자동 전송

## 설치

1. 이 폴더 전체를 GitHub 저장소로 업로드 (public이든 private이든 상관없음).
2. Teams에서 웹훅 URL 발급:
   - 해당 채널 → `...` → **Workflows** 앱 열기
   - "웹훅 요청이 수신되면 채널에 게시" (Post to a channel when a webhook
     request is received) 템플릿 선택 → 생성 → 발급된 URL 복사
   - ⚠️ 예전 "Incoming Webhook" 커넥터는 2026년 5월부로 폐지되어 더 이상
     동작하지 않습니다. 반드시 Workflows 앱에서 새로 만들어야 합니다.
3. GitHub 저장소 → Settings → Secrets and variables → Actions →
   New repository secret
   - Name: `TEAMS_WEBHOOK_URL`
   - Value: 위에서 복사한 URL
4. Actions 탭에서 워크플로우가 활성화되어 있는지 확인 (기본적으로 자동 활성화됨).

## 동작 원리

Playwright/헤드리스 브라우저 없이, 카카오 채널 페이지가 내부적으로
호출하는 공개 JSON API를 그대로 호출합니다 (인증 불필요, 크롬 개발자도구
네트워크 탭에서 실제 확인한 엔드포인트입니다):

```
GET https://pf.kakao.com/rocket-web/web/profiles/_xgUVZn/posts?includePinnedPost=true
```

가볍고 빠르며, 페이지 디자인이 바뀌어도 이 API 자체가 바뀌지 않는 한
계속 동작합니다. (카카오가 이 API를 비공식적으로 변경/중단할 가능성은
항상 있으니, 응답 형식이 바뀌면 알려주세요.)

## 스케줄

카카오 채널의 실제 게시 시각(최근 2주 데이터) 기준:

- 중식 메뉴: 매일 10:29 ~ 10:38 KST 사이 게시
- 석식 메뉴: 매일 17:04 ~ 17:12 KST 사이 게시

이를 근거로 아래 시각에 확인하도록 설정되어 있습니다:

- **10:45 KST** (`45 1 * * *` UTC) — 점심 12시 전, 가장 늦은 게시 사례보다 7분 여유
- **17:20 KST** (`20 8 * * *` UTC) — 퇴근 18시 전, 가장 늦은 게시 사례보다 8분 여유

## 즉시 요청하기 (스케줄이 돌았는데 메뉴가 비어있을 때)

카카오 쪽 게시가 평소보다 늦어져서 스케줄 실행 시점에 아직 글이 없었다면,
아래 두 방법으로 "지금 다시 확인해서 있으면 바로 보내줘" 를 요청할 수 있습니다.

### 방법 1: GitHub 웹에서 클릭

Actions 탭 → `동천한식뷔페 메뉴 -> Teams 전송` → **Run workflow**
→ (선택) `force`를 true로 체크하면 dedupe 여부와 상관없이 최신 글을
   즉시 재전송합니다. false(기본값)면 "아직 안 보낸 새 글이 있을 때만" 보냅니다.

### 방법 2: API로 원격 트리거 (휴대폰 단축어 등에 연결 가능)

1. GitHub → Settings → Developer settings → Personal access tokens →
   Fine-grained token 생성 (이 저장소에 대해 `Actions: Read and write` 권한만 부여)
2. 아래 curl을 실행하면 즉시 워크플로우가 트리거됩니다:

```bash
curl -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer <PERSONAL_ACCESS_TOKEN>" \
  https://api.github.com/repos/<OWNER>/<REPO>/dispatches \
  -d '{"event_type":"menu-check"}'
```

이 방식은 GitHub 웹 UI를 열지 않고도(예: iOS 단축어, Android 자동화 앱에서
HTTP 요청 만들기 기능으로) "지금 확인해줘"를 보낼 수 있어서, 팀즈 채널에
바로가기 버튼처럼 두고 쓰기에 적합합니다. (참고: 이 방식은 `force` 옵션 없이
항상 "새 글 있으면만 전송"으로 동작합니다.)

## 참고: 왜 자동 재시도를 추가하지 않았나

dedupe는 게시글 id 기준이라, 스케줄이든 수동 트리거든 몇 번을 다시 실행해도
"이미 보낸 글"은 중복 전송되지 않고 "아직 안 보낸 새 글"만 전송됩니다.
즉 위 두 방법 중 하나로 재요청하면 안전하게 재확인이 가능합니다.

## 알려진 한계 (검증 못 한 부분)

- **카카오 CDN 이미지 핫링크 차단 가능성**: `k.kakaocdn.net` 이미지 URL을 Teams
  MessageCard에 그대로 넣는데, 카카오가 Referer 검사로 외부(마이크로소프트 서버)
  이미지 요청을 막고 있을 경우 Teams에서 이미지가 깨져 보일 수 있습니다.
  실제 Teams 웹훅으로 테스트 전까지는 확정할 수 없는 리스크입니다.
  → 첫 실행 후 이미지가 안 보이면, 이미지를 직접 다운로드해서 base64로
    첨부하거나 임시 스토리지에 재호스팅하는 방식으로 바꿔야 합니다. 알려주시면
    바로 수정해드릴게요.
- **비공식 API 의존**: 카카오가 이 API를 예고 없이 바꾸거나 막을 수 있습니다.
  스키마가 바뀌면 실패 알림이 Teams로 오도록 되어 있으니 그때 다시 손보면 됩니다.
- **Teams Actions 실패 이메일**: 리포지토리 Settings 또는 개인 계정 알림 설정에서
  GitHub Actions 실패 시 이메일을 받도록 켜두면, Teams 웹훅 자체가 죽었을 때도
  놓치지 않을 수 있습니다.

## 첫 실행 / 디버깅

1. Actions 탭 → `동천한식뷔페 메뉴 -> Teams 전송` → `Run workflow` 로 수동 실행.
2. 로그에서 에러가 나면 그대로 저에게 붙여넣어 주세요.

## 전송 주기 변경

`.github/workflows/menu-notify.yml` 의 `cron` 값을 수정하세요.
(cron은 UTC 기준이며, KST는 UTC+9 입니다.)

## 로컬 테스트

```bash
pip install -r requirements.txt
export TEAMS_WEBHOOK_URL="https://..."
python scraper.py
```
