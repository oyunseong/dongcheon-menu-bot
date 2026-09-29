"""
동천한식뷔페 카카오톡 채널 소식(메뉴) 스크래핑 -> Teams 전송

동작 방식:
1. pf.kakao.com이 내부적으로 쓰는 공개 JSON API를 그대로 호출한다.
   (인증/쿠키 불필요, 브라우저 개발자도구 네트워크 탭에서 확인됨)
   GET https://pf.kakao.com/rocket-web/web/profiles/{채널ID}/posts?includePinnedPost=true
2. 각 게시글의 id/제목/본문/이미지 목록을 추출한다.
3. state.json에 저장된 "이미 보낸 게시글 id"와 비교해서 새 글만 Teams로 전송한다.
   dedupe 기록은 두 종류로 나뉜다:
   - sent_hashes: 일반 게시글, 최근 200개만 보관 (무한정 커지지 않도록)
   - pinned_sent_hashes: 고정(pinned) 게시글, 절대 비우지 않고 영구 보관
     (고정 글은 API 응답에 계속 나타나므로, sent_hashes처럼 오래된 기록이
     밀려나는 방식으로 관리하면 밀려난 순간 "새 글"로 재판정되어 주기적으로
     중복 전송된다. 단, 어떤 글이 보내진 뒤에 카카오 쪽에서 고정 상태가
     바뀌는 것까지는 추적하지 않는다 - 극히 드문 경우라 감수한다.)
4. 전송 후 state.json을 갱신한다. 저장에 실패하면 그 자리에서 Teams로 알린다.
5. 새 글이 없으면 같은 실행 안에서 NO_MENU_RECHECK_WAIT_SEC만큼 기다렸다가
   한 번 더 확인한다. 그래도 없으면 Teams에 안내 메시지를 보낸다.
6. 카카오 API 자체가 실패하면(다운, 스키마 변경 등) Teams에 실패 알림을 보내고
   프로세스도 실패(exit 1) 처리한다. 반면 새 글 중 일부만 Teams 전송에 실패하면
   (예: 웹훅 일시 장애) 그 글만 건너뛰고 나머지 새 글은 계속 보낸다 - 실패한
   글 하나가 뒤에 있는 다른 새 글까지 영영 막으면 안 되기 때문이다. 이 경우
   실행은 성공 처리되고, 실패한 글은 별도 경고와 함께 다음 실행에서 재시도된다.
   두 경우 모두 침묵 실패는 없다.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
import requests


def load_dotenv(path=".env"):
    """.env 파일이 있으면 KEY=VALUE 줄을 환경변수로 로드한다 (이미 설정된 값은 덮어쓰지 않음).

    launchd는 셸 프로필을 읽지 않으므로 TEAMS_WEBHOOK_URL 등을 이 파일로 넘긴다.
    """
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_dotenv()

# 다른 카카오톡 채널에 재사용하려면 KAKAO_CHANNEL_ID 환경변수만 바꾸면 된다.
CHANNEL_ID = os.environ.get("KAKAO_CHANNEL_ID", "_xgUVZn")
POSTS_API_URL = (
    f"https://pf.kakao.com/rocket-web/web/profiles/{CHANNEL_ID}/posts"
    "?includePinnedPost=true"
)
STATE_FILE = "state.json"
MAX_IMAGES_PER_MESSAGE = 9  # Teams MessageCard 섹션에서 안정적으로 렌더링되는 대략적 한도
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 3
NO_MENU_RECHECK_WAIT_SEC = 60  # 새 글이 없을 때, 같은 실행 안에서 한 번 더 확인하기까지 대기 시간

FORCE_RESEND = os.environ.get("FORCE_RESEND") == "1"  # 수동 실행 시 최신 글 강제 재전송
# 재확인까지 했는데도 새 글이 없을 때 Teams 안내를 보낼지. 기본은 항상 보냄.
# (예: 외부에서 조용히 상태만 확인하고 싶을 때 "0"으로 끌 수 있게 남겨둔 옵션)
ALERT_IF_EMPTY = os.environ.get("ALERT_IF_EMPTY", "1") == "1"
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")


def fail_fast(message):
    """설정 오류처럼 재시도로 해결 안 되는 문제는 바로 명확한 메시지로 종료."""
    print(f"[설정 오류] {message}", file=sys.stderr)
    sys.exit(1)


def with_retry(func, what):
    """네트워크 호출을 재시도(exponential backoff)와 함께 실행.

    4xx(요청/스키마 문제)는 재시도해도 해결 안 되므로 즉시 실패시키고,
    5xx/타임아웃/연결 오류만 재시도한다.
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func()
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status and 400 <= status < 500 and status != 429:
                raise  # 재시도 무의미 (예: 403/404/스키마 변경) - 즉시 상위로 전파
            last_err = e
            print(f"[재시도 {attempt}/{MAX_RETRIES}] {what} 실패(HTTP {status}): {e}")
        except requests.RequestException as e:
            last_err = e
            print(f"[재시도 {attempt}/{MAX_RETRIES}] {what} 실패: {e}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SEC * attempt)
    raise last_err


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
            state.setdefault("pinned_sent_hashes", [])
            return state
    return {"sent_hashes": [], "pinned_sent_hashes": []}


def save_state(state):
    # 최근 200개만 보관 (무한정 커지지 않도록). 단, 고정 게시글 id는 매 조회마다
    # 계속 나타나므로 이 창에서 밀려나면 "새 글"로 재판정되어 주기적으로 중복
    # 전송된다 - 그래서 pinned_sent_hashes는 따로, 절대 비우지 않고 보관한다.
    state["sent_hashes"] = state["sent_hashes"][-200:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_posts():
    """카카오 채널의 공개 JSON API에서 게시글 목록을 가져온다.

    반환: [{"id": int, "title": str, "text": str, "images": [url, ...],
             "created_at": int, "permalink": str}, ...] (최신순)
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://pf.kakao.com/{CHANNEL_ID}/posts",
        "Origin": "https://pf.kakao.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    def do_request():
        resp = requests.get(POSTS_API_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as e:
            # 카카오가 차단/유지보수 페이지(HTML)를 200으로 응답하는 경우가 있음.
            # 일시적일 가능성이 높으므로 재시도 대상으로 취급한다.
            raise requests.RequestException(f"응답이 JSON이 아닙니다: {e}") from e

    data = with_retry(do_request, "카카오 채널 API 호출")

    if "items" not in data:
        # 카카오가 응답 스키마를 바꿨을 가능성. 조용히 넘기지 않고 명확히 알린다.
        raise RuntimeError(
            f"예상치 못한 응답 스키마입니다 (items 필드 없음). 응답 키: {list(data.keys())}"
        )

    posts = []
    for item in data.get("items", []):
        if "id" not in item:
            continue  # 방어적 스킵: id 없는 항목은 dedupe 불가하므로 무시
        text_parts = [
            c.get("v", "") for c in item.get("contents", []) if c.get("t") == "text"
        ]
        images = [
            m.get("xlarge_url") or m.get("url")
            for m in item.get("media", [])
            if m.get("type") == "image"
        ]
        posts.append(
            {
                "id": item["id"],
                "title": item.get("title", ""),
                "text": "\n".join(text_parts),
                "images": [u for u in images if u],
                "created_at": item.get("created_at"),
                "permalink": item.get("permalink") or f"https://pf.kakao.com/{CHANNEL_ID}/posts",
                "pinned": bool(item.get("pinned")),
            }
        )

    # includePinnedPost=true 응답은 고정 글을 일반 목록에도 중복 포함시킬 수
    # 있다. 같은 id가 두 번 있으면 이후 dedupe 기록에 중복 저장되거나(특히
    # 절대 비우지 않는 pinned_sent_hashes) 같은 글이 한 실행 안에서 두 번
    # 전송될 수 있으므로 여기서 한 번에 정리한다.
    deduped = {}
    for post in posts:
        deduped.setdefault(post["id"], post)
    return list(deduped.values())


def _post_to_teams(payload):
    def do_request():
        resp = requests.post(TEAMS_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp

    with_retry(do_request, "Teams 웹훅 전송")


def send_to_teams(post):
    title = post["title"] or "동천한식뷔페 소식"
    body_text = post["text"][:1000]

    all_images = post["images"]
    shown_images = all_images[:MAX_IMAGES_PER_MESSAGE]
    hidden_count = len(all_images) - len(shown_images)
    if hidden_count > 0:
        body_text += f"\n\n(사진 {hidden_count}장 더 있음 - 아래 링크에서 전체보기)"

    # Workflows 웹훅은 기존 MessageCard 포맷을 그대로 지원합니다.
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": title,
        "themeColor": "0076D7",
        "title": title,
        "text": body_text,
        "potentialAction": [
            {
                "@type": "OpenUri",
                "name": "카카오톡 채널에서 전체보기",
                "targets": [{"os": "default", "uri": post["permalink"]}],
            }
        ],
        "sections": [{"images": [{"image": url} for url in shown_images]}],
    }

    _post_to_teams(payload)


def current_meal_label():
    """실행 시각(KST) 기준으로 점심/저녁 체크인지 구분한 안내용 라벨."""
    kst_hour = datetime.now(timezone(timedelta(hours=9))).hour
    return "중식(점심)" if kst_hour < 12 else "석식(저녁)"


def send_no_menu_alert():
    """스케줄 체크 시점에 아직 새 메뉴 글이 안 올라왔을 때 Teams에 안내."""
    meal_label = current_meal_label()
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": "메뉴 봇 안내",
        "themeColor": "FFC300",
        "title": f"🍽️ {meal_label} 메뉴, 아직이네요",
        "text": (
            f"동천한식뷔페에 {meal_label} 메뉴가 아직 안 올라온 것 같아요.\n\n"
            "조금 있다가 다시 올라오면 바로 알려드릴게요!"
        ),
    }
    try:
        _post_to_teams(payload)
    except requests.RequestException as e:
        print(f"[메뉴 없음 알림 전송 실패] {e}", file=sys.stderr)


def send_persist_failure_alert():
    """전송은 됐지만 중복 방지 기록 저장에 실패했을 때 Teams에 알린다."""
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": "메뉴 봇 경고",
        "themeColor": "D70000",
        "title": "⚠️ 전송 기록 저장에 실패했어요",
        "text": (
            "메뉴는 정상적으로 보냈지만, 중복 방지 기록(state.json)을 "
            "저장하는 데 실패했어요.\n\n다음 확인 때 같은 메뉴가 한 번 더 "
            "전송될 수 있어요. launchd.log를 확인해주세요."
        ),
    }
    try:
        _post_to_teams(payload)
    except requests.RequestException as e:
        print(f"[저장 실패 알림 전송도 실패] {e}", file=sys.stderr)


def send_post_failure_alert(failed_posts):
    """일부 새 글이 Teams 전송에 실패했을 때 알린다.

    스크립트 자체는 계속 진행하고 실패(exit 1) 처리하지 않는다(뒤에 있는
    다른 새 글까지 막지 않기 위함) - 대신 이 알림으로 "조용히 놓친 글"이
    생기지 않게 한다. 실패한 글은 다음 실행에서 자동으로 재시도된다.
    """
    titles = ", ".join(p["title"] for p in failed_posts)
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": "메뉴 봇 경고",
        "themeColor": "D70000",
        "title": "⚠️ 일부 메뉴 전송에 실패했어요",
        "text": (
            f"다음 글을 Teams로 보내는 데 실패했어요: {titles}\n\n"
            "다음 확인 때 자동으로 다시 시도합니다. 계속 반복되면 웹훅 URL이 "
            "살아있는지 확인해주세요."
        ),
    }
    try:
        _post_to_teams(payload)
    except requests.RequestException as e:
        print(f"[전송 실패 알림 전송도 실패] {e}", file=sys.stderr)


def send_failure_alert(error):
    """스크립트가 실패했을 때(카카오 API 실패 등) Teams에도 알린다 (침묵 실패 방지).

    이 함수는 실행을 실패 처리하는 치명적 오류 경로에서만 쓴다. 개별 글
    전송 실패처럼 회복 가능한 오류는 send_post_failure_alert를 쓴다.
    """
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": "메뉴 봇 오류",
        "themeColor": "D70000",
        "title": "⚠️ 메뉴 확인하다가 문제가 생겼어요",
        "text": (
            f"메뉴를 가져오는 중에 오류가 났어요.\n\n"
            f"오류 내용: {error}\n\n"
            f"잠시 후에 다시 시도해볼게요. 계속 반복되면 관리자에게 알려주세요."
        ),
    }
    try:
        _post_to_teams(payload)
    except requests.RequestException as e:
        # 알림 전송조차 실패하면 최소한 로그에는 남긴다.
        print(f"[실패 알림 전송도 실패] {e}", file=sys.stderr)


def _mark_sent(state, post):
    """post id를 알맞은 dedupe 버킷에 기록한다 (이미 있으면 중복 추가하지 않음).

    pinned_sent_hashes는 절대 비우지 않으므로, id 중복 유입 경로(예: 카카오
    API가 같은 글을 목록에 두 번 포함시키는 경우)가 생기면 무한히 커질 수
    있다. 두 버킷 모두에서 append 전에 존재 여부를 확인해 이를 막는다.
    """
    hash_bucket = state["pinned_sent_hashes"] if post["pinned"] else state["sent_hashes"]
    if post["id"] not in hash_bucket:
        hash_bucket.append(post["id"])


def check_and_send(state):
    """한 번 조회해서 새 글이 있으면 전송한다.

    반환값은 "새 글을 찾았는지"(전송 성공 여부와 무관)이다. main()은 이 값으로
    재확인/"메뉴 없음" 알림 여부를 결정하는데, 전송 실패를 "새 글 없음"과
    같은 값으로 취급하면 "메뉴 없음" 알림과 "전송 실패" 알림이 동시에 나가는
    모순이 생긴다. 실패한 개별 글은 다음 실행에서 자동으로 재시도된다.
    """
    posts = fetch_posts()
    if not posts:
        return False

    sent_ids = set(state["sent_hashes"]) | set(state["pinned_sent_hashes"])
    new_posts = [p for p in posts if p["id"] not in sent_ids]
    if not new_posts:
        return False

    persist_failed = False
    send_failed_posts = []
    # 오래된 것부터 순서대로 전송 (posts는 최신순이라 뒤집기)
    for post in reversed(new_posts):
        print("전송:", post["title"])
        try:
            send_to_teams(post)
        except requests.RequestException as e:
            # 이 글 하나가 계속 전송 실패한다고 해서(예: Teams 쪽 일시 장애) 뒤에
            # 있는 다른 새 글까지 영영 못 보내면 안 되므로, 여기서 멈추지 않고
            # 다음 글로 넘어간다. 실패한 글은 기록에 남기지 않아 다음 실행에서
            # 다시 시도된다. (네트워크/HTTP 오류가 아닌 버그성 예외는 여기서
            # 잡지 않고 그대로 전파시켜 실행을 실패 처리한다 - 매 실행마다 같은
            # 버그로 조용히 재시도만 반복되는 것을 막기 위함.)
            send_failed_posts.append(post)
            print(f"[경고] 전송 실패: {post['title']} ({e})", file=sys.stderr)
            continue
        _mark_sent(state, post)
        try:
            save_state(state)  # 여러 건 중 중간에 실패해도 이미 보낸 건 기록되도록 매건 저장
        except OSError as e:
            persist_failed = True
            print(f"[경고] 전송 기록 저장 실패: {e}", file=sys.stderr)
        time.sleep(1)

    if persist_failed:
        send_persist_failure_alert()
    if send_failed_posts:
        send_post_failure_alert(send_failed_posts)

    return True


def main():
    if not TEAMS_WEBHOOK_URL:
        fail_fast(
            "TEAMS_WEBHOOK_URL이 설정되지 않았습니다. "
            ".env 파일에 TEAMS_WEBHOOK_URL=... 을 추가하세요."
        )

    state = load_state()

    if FORCE_RESEND:
        # 수동 트리거로 "지금 당장 최신 글을 보내달라"는 요청.
        # dedupe 여부와 무관하게 가장 최근 글 1개를 즉시 전송한다.
        posts = fetch_posts()
        if not posts:
            print("게시글을 찾지 못했습니다. (API 응답이 비어 있음)")
            if ALERT_IF_EMPTY:
                send_no_menu_alert()
            return
        latest = posts[0]
        print("강제 전송(FORCE_RESEND):", latest["title"])
        try:
            send_to_teams(latest)
        except requests.RequestException as e:
            # 자동 스케줄 경로(check_and_send)와 동일하게, 전송 실패가 곧바로
            # 실행 실패로 이어지지 않게 한다 - "메뉴 확인 중 오류"라는 오해의
            # 소지가 있는 메시지 대신 전용 알림을 보낸다.
            print(f"[경고] 강제 전송 실패: {e}", file=sys.stderr)
            send_post_failure_alert([latest])
            return
        sent_ids = set(state["sent_hashes"]) | set(state["pinned_sent_hashes"])
        if latest["id"] not in sent_ids:
            _mark_sent(state, latest)
            try:
                save_state(state)
            except OSError as e:
                print(f"[경고] 전송 기록 저장 실패: {e}", file=sys.stderr)
                send_persist_failure_alert()
        return

    if check_and_send(state):
        return

    print(f"새 게시글 없음. {NO_MENU_RECHECK_WAIT_SEC}초 후 한 번 더 확인합니다.")
    time.sleep(NO_MENU_RECHECK_WAIT_SEC)

    if check_and_send(state):
        return

    print("재확인에도 새 게시글 없음.")
    if ALERT_IF_EMPTY:
        send_no_menu_alert()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - 최상위에서 모든 예외를 잡아 알림 후 실패 처리
        print(f"[치명적 오류] {e}", file=sys.stderr)
        if TEAMS_WEBHOOK_URL:
            send_failure_alert(e)
        sys.exit(1)
