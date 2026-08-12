"""Teams 전송 없이 카카오 채널에 새 메뉴 글이 올라왔는지만 확인하는 스크립트.

사용법:
    python check_latest.py
"""

from datetime import datetime, timezone, timedelta

import scraper

KST = timezone(timedelta(hours=9))


def main():
    posts = scraper.fetch_posts()
    print(f"조회된 글 수: {len(posts)}\n")

    for post in posts[:5]:
        created_at = post.get("created_at")
        kst_time = (
            datetime.fromtimestamp(created_at / 1000, tz=KST) if created_at else None
        )
        print(f"id: {post['id']}")
        print(f"제목: {post['title']}")
        print(f"게시 시각(KST): {kst_time}")
        print(f"본문: {post['text']}")
        print(f"이미지 수: {len(post['images'])}")
        print(f"링크: {post['permalink']}")
        print("-" * 40)


if __name__ == "__main__":
    main()
