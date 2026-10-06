"""book_class_visit tool wiring."""

import unittest

from app.core.crm_tools import AGENT_TOOL_CATALOG, agent_needs_crm_fetch
from app.core.prompt_builder import tool_source_policy
from app.core.realtime import resolve_agent_tools
from app.llc.client import LlcClient
from app.tools.definitions import OPENAI_TOOLS
from app.tools.dispatcher import _book_class_visit, dispatch_tool


def _stream():
    return {
        "client_id": "studio-1",
        "phone": "+15550001111",
        "member_id": "member-1",
        "agent": {"tools": {"book_class_visit": True, "end_call": True}},
    }


class BookClassVisitToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []

        async def fake_book(_self, **kwargs):
            self.calls.append(kwargs)
            return {
                "success": True,
                "booked": True,
                "class_name": "Body Pump",
                "say_to_user": "Body Pump is booked for Tuesday at 6:00 PM.",
            }

        self._original = LlcClient.book_class_visit
        LlcClient.book_class_visit = fake_book

    def tearDown(self):
        LlcClient.book_class_visit = self._original

    def test_catalog_and_openai_tool_exist(self):
        self.assertTrue(any(t["key"] == "book_class_visit" for t in AGENT_TOOL_CATALOG))
        self.assertTrue(any(t.get("name") == "book_class_visit" for t in OPENAI_TOOLS))

    def test_resolve_tools_honors_toggle(self):
        off = resolve_agent_tools({"tools": {"book_class_visit": False}})
        self.assertFalse(any(t.get("name") == "book_class_visit" for t in off))
        on = resolve_agent_tools({"tools": {"book_class_visit": True}})
        self.assertTrue(any(t.get("name") == "book_class_visit" for t in on))

    async def test_requires_class_name(self):
        result = await _book_class_visit({}, _stream())
        self.assertFalse(result["success"])
        self.assertEqual(self.calls, [])

    async def test_calls_llc(self):
        result = await _book_class_visit(
            {"class_name": "Body Pump", "start_time": "6:00 PM", "day": "Tuesday"},
            _stream(),
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["class_name"], "Body Pump")
        self.assertEqual(self.calls[0]["day"], "Tuesday")

    async def test_dispatch(self):
        result = await dispatch_tool(
            "book_class_visit",
            {"class_name": "Yoga", "start_time": "9:00 AM"},
            _stream(),
        )
        self.assertTrue(result["success"])

    async def test_llc_failure_stays_on_line(self):
        async def boom(_self, **_kwargs):
            return {
                "success": False,
                "error": "Pack does not cover Yoga.",
                "bookable_classes": ["Body Pump", "Power Yoga"],
                "say_to_user": (
                    "Their pack does not cover Yoga. They can book a visit for Body Pump and Power Yoga. "
                    "Ask which of those they want. Do not hang up."
                ),
            }

        LlcClient.book_class_visit = boom
        result = await _book_class_visit({"class_name": "Yoga"}, _stream())
        self.assertFalse(result["success"])
        self.assertIn("Body Pump", result["say_to_user"])
        self.assertIn("can book", result["say_to_user"].lower())

    async def test_no_pack_message_is_passed_through(self):
        async def no_pack(_self, **_kwargs):
            return {
                "success": False,
                "error": "No active class pack on this account.",
                "bookable_classes": [],
                "say_to_user": (
                    "They do not have an active class pack on their account, so Yoga cannot be booked. "
                    "Say that in one short sentence, then ask if they want to hear about a plan or the 7 Day Guest Pass. Do not hang up."
                ),
            }

        LlcClient.book_class_visit = no_pack
        result = await _book_class_visit({"class_name": "Yoga"}, _stream())
        self.assertFalse(result["success"])
        self.assertIn("do not have an active class pack", result["say_to_user"].lower())
        self.assertIn("guest pass", result["say_to_user"].lower())

    def test_prompt_and_crm_fetch_when_tool_on(self):
        agent = {"tools": {"book_class_visit": True, "end_call": True}}
        self.assertTrue(agent_needs_crm_fetch(agent))
        policy = tool_source_policy(agent)
        self.assertIn("book_class_visit", policy)
        self.assertIn("2-3 names", policy)

    async def test_missing_phone_does_not_hit_llc(self):
        info = _stream()
        info["phone"] = ""
        result = await _book_class_visit({"class_name": "Yoga"}, info)
        self.assertFalse(result["success"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
