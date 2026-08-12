"""
동천한식뷔페 카카오톡 채널 소식(메뉴) 스크래핑 -> Teams 전송

동작 방식:
1. pf.kakao.com이 내부적으로 쓰는 공개 JSON API를 그대로 호출한다.
   (인증/쿠키 불필요, 브라우저 개발자도구 네트워크 탭에서 확인됨)
   GET https://pf.kakao.com/rocket-web/web/profiles/{채널ID}/posts?includePinnedPost=true
2. 각 게시글의 id/제목/본문/이미지 목록을 추출한다.
3. state.json에 저장된 "이미 보낸 게시글 id"와 비교해서 새 글만 Teams로 전송한다.
4. 전송 후 state.json을 갱신한다 (GitHub Actions에서 커밋/푸시로 영속화).
5. 도중에 실패하면(카카오 API 다운, 스키마 변경 등) Teams에 실패 알림을
   보내고 Actions 잡도 실패 처리한다. 침묵 실패를 막기 위함.
"""

import os
import sys
import json
import time
import requests

CHANNEL_ID = "_xgUVZn"
POSTS_API_URL = (
    f"https://pf.kakao.com/rocket-web/web/profiles/{CHANNEL_ID}/posts"
    "?includePinnedPost=true"
)
STATE_FILE = "state.json"
MAX_IMAGES_PER_MESSAGE = 9  # Teams MessageCard 섹션에서 안정적으로 렌더링되는 대략적 한도
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 3

FORCE_RESEND = os.environ.get("FORCE_RESEND") == "1"  # 수동 실행 시 최신 글 강제 재전송
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")


def fail_fast(message):
    """설정 오류처럼 재시도로 해결 안 되는 문제는 바로 명확한 메시지로 종료."""
    print(f"[설정 오류] {message}", file=sys.stderr)
    sys.exit(1)


if not TEAMS_WEBHOOK_URL:
    fail_fast(
        "TEAMS_WEBHOOK_URL 환경변수(GitHub Secret)가 설정되지 않았습니다. "
        "저장소 Settings > Secrets and variables > Actions 에서 등록하세요."
    )


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
            if status and 400 <= status < 500:
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
            return json.load(f)
    return {"sent_hashes": []}


def save_state(state):
    # 최근 50개만 보관 (무한정 커지지 않도록)
    state["sent_hashes"] = state["sent_hashes"][-50:]
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
    }

    def do_request():
        resp = requests.get(POSTS_API_URL, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

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
                "permalink": item.get("permalink"),
            }
        )
    return posts


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


def send_failure_alert(error):
    """스크립트가 실패했을 때 Teams에도 알린다 (침묵 실패 방지)."""
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": "메뉴 봇 오류",
        "themeColor": "D70000",
        "title": "⚠️ 동천한식뷔페 메뉴 봇 실행 실패",
        "text": (
            f"자동 확인 중 오류가 발생해서 메뉴를 못 가져왔습니다.\n\n"
            f"오류 내용: {error}\n\n"
            f"GitHub Actions 로그를 확인하거나, 잠시 후 수동으로 재실행해주세요."
        ),
    }
    try:
        _post_to_teams(payload)
    except requests.RequestException as e:
        # 알림 전송조차 실패하면 최소한 로그에는 남긴다.
        print(f"[실패 알림 전송도 실패] {e}", file=sys.stderr)


def main():
    state = load_state()
    posts = fetch_posts()

    if not posts:
        print("게시글을 찾지 못했습니다. (API 응답이 비어 있음)")
        return

    if FORCE_RESEND:
        # 수동 트리거로 "지금 당장 최신 글을 보내달라"는 요청.
        # dedupe 여부와 무관하게 가장 최근 글 1개를 즉시 전송한다.
        latest = posts[0]
        print("강제 전송(FORCE_RESEND):", latest["title"])
        send_to_teams(latest)
        sent_ids = set(state["sent_hashes"])
        if latest["id"] not in sent_ids:
            state["sent_hashes"].append(latest["id"])
            save_state(state)
        return

    sent_ids = set(state["sent_hashes"])  # 이름은 유지하되 이제 post id를 저장
    new_posts = [p for p in posts if p["id"] not in sent_ids]

    # 오래된 것부터 순서대로 전송 (posts는 최신순이라 뒤집기)
    for post in reversed(new_posts):
        print("전송:", post["title"])
        send_to_teams(post)
        state["sent_hashes"].append(post["id"])
        save_state(state)  # 여러 건 중 중간에 실패해도 이미 보낸 건 기록되도록 매건 저장
        time.sleep(1)

    if not new_posts:
        print("새 게시글 없음. (아직 카카오에 안 올라왔거나, 이미 보낸 글뿐)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - 최상위에서 모든 예외를 잡아 알림 후 실패 처리
        print(f"[치명적 오류] {e}", file=sys.stderr)
        if TEAMS_WEBHOOK_URL:
            send_failure_alert(e)
        sys.exit(1)
