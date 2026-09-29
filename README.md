# 동천한식뷔페 메뉴 → Teams 자동 전송

동천한식뷔페 카카오톡 채널에 올라오는 중식/석식 메뉴 글을 확인해서, 새 글이 있으면
Teams 채널로 보내는 봇입니다. Mac의 launchd 스케줄러로 평일에 자동 실행됩니다.

## 실행 환경

- **macOS** (launchd 사용)
- **Python 3.9 이상** (macOS 기본 `python3`로 충분. 없으면 `xcode-select --install`)
- 의존성: `requests` (`install.sh`가 `.venv`에 자동 설치)
- 설정: 저장소에 포함된 `.env`

```
TEAMS_WEBHOOK_URL=https://...     # 필수. Teams Workflows 웹훅 URL
# KAKAO_CHANNEL_ID=_xgUVZn        # 선택. 다른 카카오 채널을 쓸 때만
```

> 로그에 뜨는 `NotOpenSSLWarning`(LibreSSL 관련)은 macOS 기본 Python에서
> 나오는 경고이며 동작에는 영향이 없습니다.

## 설치 (새 Mac에서)

```bash
git clone https://github.com/oyunseong/dongcheon-menu-bot.git
cd dongcheon-menu-bot
./install.sh
```

`install.sh`가 하는 일:

1. `.venv` 가상환경 생성 + `requirements.txt` 설치
2. `~/Library/LaunchAgents/`에 plist 2개를 현재 폴더 경로 기준으로 생성해서 등록
   - `com.menubot.lunch` — 평일(월~금) **10:45** 중식 체크
   - `com.menubot.dinner` — 평일(월~금) **17:20** 석식 체크

여러 번 실행해도 안전합니다(기존 등록을 덮어씀). 폴더를 옮겼다면 다시 실행하세요.

등록 확인:

```bash
launchctl list | grep menubot
```

⚠️ **봇은 한 대의 Mac에서만 등록하세요.** 두 대에 등록돼 있으면 메뉴가 두 번씩
전송됩니다. 다른 Mac으로 옮길 때는 기존 Mac에서 `./uninstall.sh`를 실행하세요.

## 해제

```bash
./uninstall.sh
```

launchd 등록을 해제하고 plist를 삭제합니다. 코드, `.venv`, 로그는 그대로 둡니다.

## 수동 실행

```bash
.venv/bin/python3 scraper.py                   # 스케줄과 동일하게 1회 실행
FORCE_RESEND=1 .venv/bin/python3 scraper.py    # 이미 보낸 글이어도 최신 글 강제 재전송
.venv/bin/python3 check_latest.py              # Teams 전송 없이 최근 글 5개만 출력
.venv/bin/python3 test_webhook.py              # 최신 글 1건을 Teams로 전송 (state.json 변경 없음)
.venv/bin/python3 test_webhook.py --dummy      # 카카오 호출 없이 더미 카드만 전송
```

실행 로그는 `launchd.log`에 쌓입니다:

```bash
tail -f launchd.log
```

## 파일 구성

| 파일 | 역할 |
|---|---|
| `scraper.py` | 본체. 카카오 API 조회 → 새 글 판별 → Teams 전송 → `state.json` 갱신 |
| `state.json` | 이미 보낸 게시글 id 기록 (중복 전송 방지). 실행하면 자동 갱신됨 |
| `.env` | `TEAMS_WEBHOOK_URL` 등 설정. `scraper.py`가 시작할 때 자동으로 읽음 |
| `install.sh` | venv 준비 + launchd 스케줄 등록 |
| `uninstall.sh` | launchd 스케줄 해제 |
| `check_latest.py` | 카카오 채널 최근 글 확인용 (전송 안 함) |
| `test_webhook.py` | Teams 웹훅 전송 테스트용 (`state.json` 안 건드림) |
| `tests/test_scraper.py` | 네트워크 없이 파싱/재시도/dedupe 로직을 검증하는 pytest |
| `requirements.txt` | 실행 의존성 (`requests`) |
| `requirements-dev.txt` | 테스트 의존성 (`pytest`) |
| `launchd.log` | 스케줄 실행 로그 (git 제외) |
| `.github/workflows/test.yml` | push/PR 때 pytest 자동 실행 (메뉴 전송과 무관) |
| `.github/dependabot.yml` | 의존성 버전 업데이트 알림 |

## 동작 원리

1. 카카오 채널 페이지가 내부적으로 호출하는 공개 JSON API를 그대로 호출합니다
   (인증 불필요, 브라우저 없이 동작).
   ```
   GET https://pf.kakao.com/rocket-web/web/profiles/_xgUVZn/posts?includePinnedPost=true
   ```
2. `state.json`의 "이미 보낸 id"와 비교해서 **새 글만** Teams로 보냅니다.
   몇 번을 다시 실행해도 같은 글은 중복 전송되지 않습니다.
3. 새 글이 없으면 60초 기다렸다가 한 번 더 확인하고, 그래도 없으면 Teams에
   "아직 메뉴가 없다"는 안내를 보냅니다.
4. 카카오 API 실패(다운, 응답 형식 변경 등)나 Teams 전송 실패 시에도 Teams로
   경고를 보냅니다. 조용히 실패하지 않습니다.

### 스케줄 시각 기준

카카오 채널의 실제 게시 시각(관찰 기준): 중식 10:29~10:38, 석식 17:04~17:12.
여유를 두고 10:45 / 17:20에 확인합니다. 시각을 바꾸려면 `install.sh` 맨 아래
`write_plist` 줄의 시/분을 수정한 뒤 `./install.sh`를 다시 실행하세요.

### state.json

- `sent_hashes`: 일반 게시글 id, 최근 200개만 보관
- `pinned_sent_hashes`: 고정(핀) 게시글 id, 영구 보관
  (고정 글은 API 응답에 계속 나타나므로 기록이 밀려나면 다시 "새 글"로 판정됨)

컴퓨터마다 따로 관리됩니다. 새 Mac은 clone한 시점의 기록에서 시작합니다.

## 알려진 한계

- **Mac이 꺼져 있으면 실행되지 않습니다.** 잠자기 상태면 깨어난 직후 실행됩니다.
  정시에 깨우려면 `sudo pmset repeat wake MTWRF 10:44:00` 처럼 깨우기 예약을
  걸 수 있습니다(하루 한 시각만 지정 가능).
- **비공식 API 의존**: 카카오가 API를 예고 없이 바꾸면 동작하지 않을 수 있습니다.
  이 경우 Teams로 실패 알림이 옵니다.
- Teams 예전 "Incoming Webhook" 커넥터는 폐지되었습니다. 웹훅 URL을 새로 만들
  때는 채널 → `...` → **Workflows** → "웹훅 요청이 수신되면 채널에 게시" 템플릿을
  사용하세요.

## 다른 카카오톡 채널에 재사용

`.env`에 `KAKAO_CHANNEL_ID=<채널ID>`를 추가하면 됩니다. 채널 ID는
`pf.kakao.com/<채널ID>` 주소에서 확인할 수 있습니다. 기본값은 동천한식뷔페(`_xgUVZn`).

## 단위 테스트

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/ -v
```
