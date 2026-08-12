"""Teams 웹훅에 실제로 카드를 쏴보는 테스트 스크립트. state.json은 건드리지 않는다.

사용법:
    export TEAMS_WEBHOOK_URL="https://..."
    python test_webhook.py            # 카카오 최신 글 1건을 그대로 전송
    python test_webhook.py --dummy    # 카카오 호출 없이 더미 카드만 전송 (포맷/렌더링만 확인)
"""

import sys

import scraper

if not scraper.TEAMS_WEBHOOK_URL:
    print("[설정 오류] TEAMS_WEBHOOK_URL 환경변수가 없습니다.", file=sys.stderr)
    sys.exit(1)


def build_dummy_post():
    return {
        "title": "[테스트] 동천 중식 메뉴입니다~",
        "text": "웹훅 포맷 확인용 더미 메시지입니다.",
        "images": [
            "https://k.kakaocdn.net/dn/oWeRQ/dJMcag7yTn9/grGVq5MP7T2N89K3sBIJAK/img_xl.jpg"
        ],
        "permalink": "http://pf.kakao.com/_xgUVZn/posts",
    }


def main():
    if "--dummy" in sys.argv:
        post = build_dummy_post()
        print("더미 카드 전송 중...")
    else:
        posts = scraper.fetch_posts()
        if not posts:
            print("카카오 채널에 글이 없습니다.")
            return
        post = posts[0]
        print("최신 글 전송 중:", post["title"])

    scraper.send_to_teams(post)
    print("전송 완료. (state.json은 변경하지 않았습니다)")


if __name__ == "__main__":
    main()
