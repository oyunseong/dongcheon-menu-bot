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

**평일(월~금)에만** 아래 시각에 확인하도록 설정되어 있습니다:

- **10:45 KST** (`45 1 * * 1-5` UTC) — 중식 메뉴 체크, 점심 12시 전 여유 확보
- **17:20 KST** (`20 8 * * 1-5` UTC) — 석식 메뉴 체크, 퇴근 18시 전 여유 확보

새 글을 못 찾으면 별도 cron 없이 **같은 실행 안에서 1분 기다렸다가 한 번 더
확인**합니다 (GitHub Actions 스케줄은 부하 시 몇 분씩 지연될 수 있어서, 재확인을
별도 cron으로 나누면 순서가 뒤바뀔 위험이 있어 스크립트 내부에서 처리). 재확인
에도 없으면 그때 Teams에 안내 메시지를 보냅니다.

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

⚠️ 이 토큰은 이 저장소의 워크플로우를 임의로 실행할 수 있는 권한을 가집니다.
휴대폰에 저장할 경우 분실/유출에 주의하고, 더 이상 안 쓰면 GitHub → Settings →
Developer settings → Personal access tokens 에서 바로 폐기(revoke)하세요.

## 참고: 왜 자동 재시도를 추가하지 않았나

dedupe는 게시글 id 기준이라, 스케줄이든 수동 트리거든 몇 번을 다시 실행해도
"이미 보낸 글"은 중복 전송되지 않고 "아직 안 보낸 새 글"만 전송됩니다.
즉 위 두 방법 중 하나로 재요청하면 안전하게 재확인이 가능합니다.

## 중복 방지 기록(state.json)은 어떻게 저장되나

보낸 글의 id는 `state.json`에 두 목록으로 나눠 저장해서 dedupe에 씁니다.

- `sent_hashes`: 일반 게시글 id, 최근 200개만 보관합니다.
- `pinned_sent_hashes`: 고정(핀) 게시글 id, 절대 비우지 않고 계속 쌓습니다.
  고정 글은 카카오 API 응답에 계속 나타나기 때문에, 일반 글처럼 오래된
  기록을 지우는 방식으로 관리하면 지워지는 순간 "새 글"로 다시 판정되어
  같은 고정 글이 주기적으로 중복 전송되는 문제가 생깁니다.

GitHub Actions에서는
git commit/push가 아니라, 스크립트 안에서 **GitHub Contents API로 저장소의
state.json을 직접 갱신**합니다 (sha 기반 compare-and-swap: 다른 실행이 먼저
갱신해서 sha가 안 맞으면 최신 sha로 재시도). 로컬 git 상태나 rebase 충돌 같은
불투명한 실패 모드가 없고, 5번 재시도 후에도 최종 실패하면 "메뉴는 보냈지만
기록 저장에 실패했다"는 경고를 즉시 Teams로 보냅니다. (로컬에서 실행할 때는
`GITHUB_TOKEN`이 없으니 로컬 파일에만 저장되고 원격 동기화는 자동으로 건너뜁니다.)

## 알려진 한계

- **비공식 API 의존**: 카카오가 이 API를 예고 없이 바꾸거나 막을 수 있습니다.
  스키마가 바뀌면 실패 알림이 Teams로 오도록 되어 있으니 그때 다시 손보면 됩니다.
- **60일 이상 저장소 변경이 없으면 GitHub가 스케줄 워크플로우를 자동으로
  끕니다.** state.json은 **새 글을 실제로 보낸 실행에서만** 갱신되므로(새 글이
  없으면 커밋도 없음), 식당이 장기 휴업해서 몇 달간 새 글이 하나도 없으면 이
  자동 갱신으로는 막을 수 없고 조용히 스케줄이 꺼질 수 있습니다. Actions 탭에서
  가끔 최근 실행 여부를 확인해주세요.
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

## 다른 카카오톡 채널에 재사용하기

`KAKAO_CHANNEL_ID` 환경변수(GitHub Actions라면 Settings → Secrets and
variables → Actions → Variables에 등록)로 채널을 바꿀 수 있습니다. 기본값은
동천한식뷔페(`_xgUVZn`)입니다. 다른 채널로 바꾸려면 코드 수정 없이 이 값만
바꾸면 됩니다.

## 단위 테스트

네트워크 호출 없이 파싱/재시도/dedupe 로직만 검증하는 pytest 테스트가
있습니다. `.github/workflows/test.yml`에서 push/PR마다 자동으로 돕니다.

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```
