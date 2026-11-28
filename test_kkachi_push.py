import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

import notify
from kkachi_push import PushRejected, PushTarget, briefing_data


PAGE = "425857fe-7a80-814d-a08c-de915c14ff95"
URL = "https://www.notion.so/425857fe7a80814da08cde915c14ff95"
IDS = {"notify_db": "notion-test-db", "user_id": "test-user"}
TARGET = PushTarget("kkachi-post-test", "sample-device-token", "sample-access-token")


class PushTest(unittest.TestCase):
    def test_payload_matches_android_contract_and_stays_under_size_limit(self):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"name":"fixture"}')) as opened:
            TARGET.send(PAGE, "오늘의 브리핑" * 60, ["오늘 공개 소식" * 500], URL)
        req = opened.call_args.args[0]
        data = json.loads(req.data)["message"]
        self.assertEqual(data["data"]["kind"], "works-briefing")
        self.assertEqual(data["data"]["id"], PAGE.replace("-", ""))
        self.assertEqual(data["data"]["url"], URL)
        self.assertEqual(data["android"]["priority"], "high")
        self.assertNotIn("notification", data)
        self.assertLess(len(req.data), 4096)
        self.assertEqual(req.get_method(), "POST")

    def test_invalid_source_is_rejected_before_network(self):
        for url in ("https://example.invalid", "http://www.notion.so/page", "https://x@www.notion.so/page"):
            with self.subTest(url=url), self.assertRaises(PushRejected):
                briefing_data(PAGE, "시험", [], url)

    def test_only_definitive_http_error_is_safe_to_fall_back(self):
        for code, kind in ((401, PushRejected), (404, PushRejected), (408, RuntimeError), (500, RuntimeError)):
            error = urllib.error.HTTPError("https://fcm.googleapis.com", code, "fixture", None, None)
            with self.subTest(code=code), patch("urllib.request.urlopen", side_effect=error), self.assertRaises(kind):
                TARGET.send(PAGE, "시험", [], URL)

    def send_card(self, route, *, hour=9, label=notify.BRIEFING_LABEL, target=TARGET, error=None):
        with (patch.dict(os.environ, {"KKACHI_BRIEFING_ROUTE": route}, clear=True),
              patch.object(notify, "_ids", return_value=IDS),
              patch.object(notify, "_post", return_value={"id": PAGE, "url": URL}) as notion,
              patch.object(PushTarget, "from_environment", return_value=target),
              patch.object(PushTarget, "send", side_effect=error) as send):
            notify._send_now(label, ["요약"], [], [], None, "2026-11-28", hour)
        return notion, send

    def test_inactive_or_unscheduled_cards_keep_notion_mentions(self):
        for route, values in (("", {}), ("direct", {"target": None}),
                              ("direct", {"label": "자동 실행 실패"}), ("direct", {"hour": None})):
            with self.subTest(route=route, values=values):
                notion, send = self.send_card(route, **values)
                self.assertEqual(notion.call_args.args[1]["children"][0]["paragraph"]["rich_text"][0]["type"], "mention")
                send.assert_not_called()

    def test_direct_both_slots_save_unmentioned_then_send_page_link(self):
        for hour in (9, 19):
            with self.subTest(hour=hour):
                notion, send = self.send_card("direct", hour=hour)
                self.assertEqual(notion.call_count, 1)
                self.assertEqual(notion.call_args.args[1]["children"][0]["paragraph"]["rich_text"][0]["type"], "text")
                self.assertEqual(send.call_args.args[0], PAGE)
                self.assertEqual(send.call_args.args[3], URL)

    def test_rejected_push_adds_one_mention_to_the_saved_page(self):
        notion, send = self.send_card("direct", error=PushRejected("fixture"))
        send.assert_called_once()
        self.assertEqual(notion.call_count, 2)
        args = notion.call_args
        self.assertEqual(args.args[0], f"/blocks/{PAGE}/children")
        self.assertEqual(args.kwargs["method"], "PATCH")
        self.assertEqual(args.args[1]["children"][0]["paragraph"]["rich_text"][0]["type"], "mention")


if __name__ == "__main__":
    unittest.main()
