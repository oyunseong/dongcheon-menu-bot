import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("TEAMS_WEBHOOK_URL", "https://example.invalid/webhook")

import scraper  # noqa: E402


def make_response(status_code=200, json_data=None, json_error=None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_error is not None:
        resp.json.side_effect = json_error
    else:
        resp.json.return_value = json_data
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


SAMPLE_ITEM = {
    "id": 1001,
    "title": "동천 중식 메뉴입니다~",
    "contents": [{"t": "text", "v": "맛있게 드시러 오세요^^"}],
    "media": [
        {"type": "image", "xlarge_url": "https://img.example/1.jpg"},
        {"type": "image", "url": "https://img.example/2.jpg"},
    ],
    "created_at": 1700000000000,
    "permalink": "http://pf.kakao.com/_xgUVZn/1001",
}


def test_fetch_posts_parses_items(monkeypatch):
    resp = make_response(json_data={"items": [SAMPLE_ITEM]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))

    posts = scraper.fetch_posts()

    assert len(posts) == 1
    post = posts[0]
    assert post["id"] == 1001
    assert post["title"] == "동천 중식 메뉴입니다~"
    assert post["text"] == "맛있게 드시러 오세요^^"
    assert post["images"] == ["https://img.example/1.jpg", "https://img.example/2.jpg"]


def test_fetch_posts_skips_items_without_id(monkeypatch):
    no_id_item = {k: v for k, v in SAMPLE_ITEM.items() if k != "id"}
    resp = make_response(json_data={"items": [no_id_item, SAMPLE_ITEM]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))

    posts = scraper.fetch_posts()

    assert len(posts) == 1
    assert posts[0]["id"] == 1001


def test_fetch_posts_tags_pinned_items(monkeypatch):
    pinned_item = dict(SAMPLE_ITEM, id=2002, pinned=True)
    resp = make_response(json_data={"items": [pinned_item, SAMPLE_ITEM]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))

    posts = scraper.fetch_posts()

    by_id = {p["id"]: p for p in posts}
    assert by_id[2002]["pinned"] is True
    assert by_id[1001]["pinned"] is False


def test_fetch_posts_falls_back_to_channel_url_when_permalink_missing(monkeypatch):
    no_permalink_item = {k: v for k, v in SAMPLE_ITEM.items() if k != "permalink"}
    resp = make_response(json_data={"items": [no_permalink_item]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))

    posts = scraper.fetch_posts()

    assert posts[0]["permalink"] == f"https://pf.kakao.com/{scraper.CHANNEL_ID}/posts"


def test_fetch_posts_dedupes_items_with_same_id(monkeypatch):
    """includePinnedPost=true 응답은 고정 글을 일반 목록에도 중복 포함시킬 수
    있다. 같은 id가 두 번 오면 한 번만 남겨서, 같은 실행에서 두 번 전송되거나
    dedupe 기록(특히 절대 안 비우는 pinned_sent_hashes)에 중복 저장되는 것을
    막아야 한다."""
    duplicate = dict(SAMPLE_ITEM, pinned=True)
    resp = make_response(json_data={"items": [duplicate, dict(SAMPLE_ITEM)]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))

    posts = scraper.fetch_posts()

    assert [p["id"] for p in posts] == [1001]


def test_fetch_posts_raises_on_unexpected_schema(monkeypatch):
    resp = make_response(json_data={"unexpected": True})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))

    with pytest.raises(RuntimeError):
        scraper.fetch_posts()


def test_with_retry_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.HTTPError(response=SimpleNamespace(status_code=429))
        return "ok"

    result = scraper.with_retry(flaky, "테스트")
    assert result == "ok"
    assert calls["n"] == 2


def test_with_retry_raises_immediately_on_404(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())

    def always_404():
        raise requests.HTTPError(response=SimpleNamespace(status_code=404))

    with pytest.raises(requests.HTTPError):
        scraper.with_retry(always_404, "테스트")


def test_fetch_posts_retries_on_non_json_response(monkeypatch):
    bad_resp = make_response(status_code=200, json_error=ValueError("not json"))
    good_resp = make_response(json_data={"items": [SAMPLE_ITEM]})
    monkeypatch.setattr(
        scraper.requests, "get", MagicMock(side_effect=[bad_resp, good_resp])
    )
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())

    posts = scraper.fetch_posts()

    assert len(posts) == 1


def test_check_and_send_only_sends_unsent_posts(monkeypatch, tmp_path):
    already_sent = dict(SAMPLE_ITEM)
    already_sent["id"] = 999
    resp = make_response(json_data={"items": [SAMPLE_ITEM, already_sent]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())

    sent = []
    monkeypatch.setattr(scraper, "send_to_teams", lambda post: sent.append(post["id"]))
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))

    state = {"sent_hashes": [999], "pinned_sent_hashes": []}
    found_new = scraper.check_and_send(state)

    assert found_new is True
    assert sent == [1001]
    assert 1001 in state["sent_hashes"]


def test_check_and_send_returns_false_when_nothing_new(monkeypatch, tmp_path):
    resp = make_response(json_data={"items": [SAMPLE_ITEM]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))

    sent = []
    monkeypatch.setattr(scraper, "send_to_teams", lambda post: sent.append(post["id"]))

    state = {"sent_hashes": [1001], "pinned_sent_hashes": []}
    found_new = scraper.check_and_send(state)

    assert found_new is False
    assert sent == []


def test_check_and_send_routes_pinned_post_id_to_pinned_bucket(monkeypatch, tmp_path):
    """이미 pinned_sent_hashes에 있는 id는 sent_hashes에 없어도 "이미 보낸
    글"로 인식돼야 한다 (버킷 조회가 union으로 이뤄지는지 확인)."""
    pinned_item = dict(SAMPLE_ITEM, id=2002, pinned=True)
    resp = make_response(json_data={"items": [pinned_item]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))

    sent = []
    monkeypatch.setattr(scraper, "send_to_teams", lambda post: sent.append(post["id"]))

    state = {"sent_hashes": [], "pinned_sent_hashes": [2002]}
    found_new = scraper.check_and_send(state)

    assert found_new is False
    assert sent == []


def test_pinned_post_survives_sent_hashes_eviction_and_is_not_resent(
    monkeypatch, tmp_path
):
    """고정 게시글을 실제로 한 번 전송한 뒤, sent_hashes가 200개 제한으로
    실제 트림될 만큼 다른 일반 글을 계속 보내도(고정 글 id는 sent_hashes에
    애초에 안 들어가므로 트림과 무관하게) 고정 글이 다시 "새 글"로 오판되어
    재전송되지 않아야 한다 - 이 시나리오가 round-2에서 고치려던 버그다."""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(scraper, "STATE_FILE", str(state_file))
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())
    sent = []
    monkeypatch.setattr(scraper, "send_to_teams", lambda post: sent.append(post["id"]))

    state = {"sent_hashes": [], "pinned_sent_hashes": []}

    pinned_item = dict(SAMPLE_ITEM, id=2002, pinned=True)
    resp = make_response(json_data={"items": [pinned_item]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    assert scraper.check_and_send(state) is True
    assert sent == [2002]
    assert state["pinned_sent_hashes"] == [2002]

    # 일반 글을 sent_hashes 한도(200)보다 많이 보내 실제로 트림이 일어나게 한다.
    for i in range(3, 3 + 210):
        item = dict(SAMPLE_ITEM, id=i, pinned=False)
        resp = make_response(json_data={"items": [item]})
        monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
        scraper.check_and_send(state)

    assert len(state["sent_hashes"]) == 200
    assert 2002 not in state["sent_hashes"]  # 트림 이후에도 sent_hashes에는 없음

    # 고정 글이 다시 API 응답에 나타나도(현실적으로 항상 그렇다) 재전송되면 안 됨.
    resp = make_response(json_data={"items": [pinned_item]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    found_new = scraper.check_and_send(state)

    assert found_new is False
    assert sent.count(2002) == 1  # 고정 글은 딱 한 번만 전송됨


def test_save_state_caps_history(tmp_path):
    state_file = tmp_path / "state.json"
    scraper.STATE_FILE = str(state_file)
    try:
        scraper.save_state({"sent_hashes": list(range(250))})
        saved = json.loads(state_file.read_text())
        assert len(saved["sent_hashes"]) == 200
        assert saved["sent_hashes"][-1] == 249
    finally:
        scraper.STATE_FILE = "state.json"


def test_main_retries_once_then_alerts_if_still_empty(monkeypatch, tmp_path):
    resp = make_response(json_data={"items": []})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(scraper, "FORCE_RESEND", False)

    sleeps = []
    monkeypatch.setattr(scraper.time, "sleep", lambda s: sleeps.append(s))

    alerts = []
    monkeypatch.setattr(scraper, "send_no_menu_alert", lambda: alerts.append(1))
    monkeypatch.setattr(scraper, "ALERT_IF_EMPTY", False)
    scraper.main()
    assert alerts == []
    assert sleeps == [scraper.NO_MENU_RECHECK_WAIT_SEC]

    monkeypatch.setattr(scraper, "ALERT_IF_EMPTY", True)
    scraper.main()
    assert alerts == [1]


def test_main_finds_post_on_recheck_without_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(scraper, "FORCE_RESEND", False)
    monkeypatch.setattr(scraper, "ALERT_IF_EMPTY", True)
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())

    empty_resp = make_response(json_data={"items": []})
    found_resp = make_response(json_data={"items": [SAMPLE_ITEM]})
    monkeypatch.setattr(
        scraper.requests, "get", MagicMock(side_effect=[empty_resp, found_resp])
    )

    sent = []
    monkeypatch.setattr(scraper, "send_to_teams", lambda post: sent.append(post["id"]))
    alerts = []
    monkeypatch.setattr(scraper, "send_no_menu_alert", lambda: alerts.append(1))

    scraper.main()

    assert sent == [1001]
    assert alerts == []


def test_check_and_send_keeps_sending_later_posts_after_one_send_fails(
    monkeypatch, tmp_path
):
    """오래된 글 전송이 실패해도(예: Teams 쪽 문제) 그보다 새 글은 계속
    전송돼야 한다 - 실패한 글 하나가 이후 새 글 전체를 영영 막으면 안 됨."""
    older = dict(SAMPLE_ITEM, id=1000, title="실패할 글")
    newer = dict(SAMPLE_ITEM, id=1001, title="성공할 글")
    resp = make_response(json_data={"items": [newer, older]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())

    sent = []

    def fake_send(post):
        if post["id"] == 1000:
            raise requests.RequestException("웹훅 실패")
        sent.append(post["id"])

    monkeypatch.setattr(scraper, "send_to_teams", fake_send)
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))
    alerts = []
    monkeypatch.setattr(scraper, "send_post_failure_alert", lambda posts: alerts.append(posts))

    state = {"sent_hashes": [], "pinned_sent_hashes": []}
    found_new = scraper.check_and_send(state)

    assert found_new is True
    assert sent == [1001]
    assert state["sent_hashes"] == [1001]  # 실패한 1000은 기록되지 않아 다음에 재시도됨
    assert len(alerts) == 1
    assert alerts[0][0]["id"] == 1000


def test_check_and_send_non_network_exception_propagates_instead_of_being_swallowed(
    monkeypatch, tmp_path
):
    """웹훅 장애가 아니라 페이로드 구성 버그 같은 프로그래밍 오류는 개별 글
    실패로 조용히 삼켜서 매 실행마다 재시도만 반복하면 안 되고, 그대로
    전파되어 잡을 실패 처리해야 한다."""
    resp = make_response(json_data={"items": [SAMPLE_ITEM]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))

    def buggy_send(post):
        raise KeyError("payload 구성 버그")

    monkeypatch.setattr(scraper, "send_to_teams", buggy_send)

    with pytest.raises(KeyError):
        scraper.check_and_send({"sent_hashes": [], "pinned_sent_hashes": []})


def test_main_does_not_send_no_menu_alert_when_new_post_failed_to_send(
    monkeypatch, tmp_path
):
    """새 글은 있었지만 전송에 실패한 경우, main()의 재확인 루프가 이를
    "메뉴 없음"으로 오판해서 앞서 보낸 전송-실패 알림과 모순되는 "메뉴가 아직
    안 올라왔다" 알림을 추가로 보내면 안 된다."""
    resp = make_response(json_data={"items": [SAMPLE_ITEM]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(scraper, "FORCE_RESEND", False)
    monkeypatch.setattr(scraper, "ALERT_IF_EMPTY", True)
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())

    def failing_send(post):
        raise requests.RequestException("웹훅 다운")

    monkeypatch.setattr(scraper, "send_to_teams", failing_send)
    post_failure_alerts = []
    monkeypatch.setattr(
        scraper, "send_post_failure_alert", lambda posts: post_failure_alerts.append(posts)
    )
    no_menu_alerts = []
    monkeypatch.setattr(scraper, "send_no_menu_alert", lambda: no_menu_alerts.append(1))

    scraper.main()

    assert len(post_failure_alerts) == 1  # 전송 실패 알림은 정확히 한 번
    assert no_menu_alerts == []  # 모순되는 "메뉴 없음" 알림은 없어야 함


def test_main_force_resend_send_failure_does_not_fail_job(monkeypatch, tmp_path):
    """FORCE_RESEND 경로도 자동 스케줄 경로와 동일하게, 웹훅 전송 실패가
    "카카오 API 조회 실패"로 오인되는 범용 알림 대신 전용 알림을 보내고,
    예외를 전파시켜 잡을 실패 처리하지 않아야 한다."""
    resp = make_response(json_data={"items": [SAMPLE_ITEM]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(scraper, "FORCE_RESEND", True)

    def failing_send(post):
        raise requests.RequestException("웹훅 다운")

    monkeypatch.setattr(scraper, "send_to_teams", failing_send)
    post_failure_alerts = []
    monkeypatch.setattr(
        scraper, "send_post_failure_alert", lambda posts: post_failure_alerts.append(posts)
    )
    generic_failure_alerts = []
    monkeypatch.setattr(
        scraper, "send_failure_alert", lambda e: generic_failure_alerts.append(e)
    )

    scraper.main()  # 예외 없이 끝나야 함(잡 실패로 이어지면 안 됨)

    assert len(post_failure_alerts) == 1
    assert generic_failure_alerts == []


def test_current_meal_label_am_pm(monkeypatch):
    class FixedDatetime:
        @classmethod
        def now(cls, tz):
            return SimpleNamespace(hour=10)

    monkeypatch.setattr(scraper, "datetime", FixedDatetime)
    assert scraper.current_meal_label() == "중식(점심)"

    class FixedDatetimePM:
        @classmethod
        def now(cls, tz):
            return SimpleNamespace(hour=18)

    monkeypatch.setattr(scraper, "datetime", FixedDatetimePM)
    assert scraper.current_meal_label() == "석식(저녁)"


def test_check_and_send_alerts_when_persist_fails(monkeypatch, tmp_path):
    resp = make_response(json_data={"items": [SAMPLE_ITEM]})
    monkeypatch.setattr(scraper.requests, "get", MagicMock(return_value=resp))
    monkeypatch.setattr(scraper, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(scraper.time, "sleep", MagicMock())
    monkeypatch.setattr(scraper, "send_to_teams", lambda post: None)

    def boom(state):
        raise OSError("디스크 쓰기 실패")

    monkeypatch.setattr(scraper, "save_state", boom)
    alerts = []
    monkeypatch.setattr(scraper, "send_persist_failure_alert", lambda: alerts.append(1))

    found_new = scraper.check_and_send({"sent_hashes": [], "pinned_sent_hashes": []})

    assert found_new is True
    assert alerts == [1]
