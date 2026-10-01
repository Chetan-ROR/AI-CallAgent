"""Checks for the 7 Day Guest Pass call fixes. No live Mindbody or phone call."""

import asyncio
import time
import unittest
from types import SimpleNamespace

from app.core.prompt_builder import tool_source_policy
from app.llc.client import LlcClient
from app.openai.media_stream import _event_transcript, _playback_protected, _session_config
from app.tools.definitions import OPENAI_TOOLS
from app.tools.dispatcher import (
    _book_guest_pass,
    guest_pass_agreement,
    note_caller_transcript,
    note_caller_turn_after_offer,
    note_guest_pass_offer,
    protect_guest_pass_playback,
)


def _stream():
    return {
        "client_id": "studio-1",
        "phone": "+15550001111",
        "member_id": "member-1",
        "guest_pass_offered": False,
        "guest_pass_booked": False,
        "caller_turns_after_offer": 0,
        "caller_utterances_after_offer": [],
        "protect_playback_until": 0,
    }


def _offer_then(text: str) -> dict:
    info = _stream()
    note_guest_pass_offer(info, "Would you like the 7 Day Guest Pass?")
    note_caller_turn_after_offer(info)
    note_caller_transcript(info, text)
    return info


class GuestPassAgreementTests(unittest.TestCase):
    def test_greeting_yes_does_not_book(self):
        info = _stream()
        note_caller_transcript(info, "yes")
        note_caller_turn_after_offer(info)
        ok, message = guest_pass_agreement(info)
        self.assertFalse(ok)
        self.assertIn("Do not book yet", message)

    def test_offer_without_an_answer_does_not_book(self):
        info = _stream()
        note_guest_pass_offer(info, "Would you like the 7 Day Guest Pass?")
        ok, message = guest_pass_agreement(info)
        self.assertFalse(ok)
        self.assertIn("not answered", message)

    def test_clear_yes_after_offer_books(self):
        for phrase in ("yes", "sure, book it", "haan", "theek hai"):
            ok, _message = guest_pass_agreement(_offer_then(phrase))
            self.assertTrue(ok, phrase)

    def test_no_after_offer_does_not_book(self):
        ok, message = guest_pass_agreement(_offer_then("no thanks"))
        self.assertFalse(ok)
        self.assertIn("did not agree", message)

    def test_unclear_answer_does_not_book(self):
        ok, message = guest_pass_agreement(_offer_then("what is that"))
        self.assertFalse(ok)
        self.assertIn("not clearly agreed", message)

    def test_caller_can_ask_for_the_pass(self):
        info = _stream()
        note_caller_transcript(info, "I want the 7 day guest pass")
        ok, _message = guest_pass_agreement(info)
        self.assertTrue(ok)

    def test_yes_before_the_offer_is_cleared(self):
        info = _stream()
        note_caller_transcript(info, "yes")
        note_guest_pass_offer(info, "Would you like the 7 Day Guest Pass?")
        ok, _message = guest_pass_agreement(info)
        self.assertFalse(ok)


class GuestPassBookingGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []

        async def fake_book(_self, **kwargs):
            self.calls.append(kwargs)
            return {"success": True, "member_id": "member-1", "plan": "7 Day Guest Pass"}

        self._original = LlcClient.book_guest_pass
        LlcClient.book_guest_pass = fake_book

    def tearDown(self):
        LlcClient.book_guest_pass = self._original

    async def test_tool_does_not_call_rails_before_yes(self):
        info = _stream()
        note_caller_transcript(info, "yes we can talk")
        result = await _book_guest_pass({}, info)
        self.assertFalse(result["success"])
        self.assertEqual(self.calls, [])
        self.assertIn("say_to_user", result)

    async def test_tool_calls_rails_after_yes(self):
        info = _offer_then("yes")
        result = await _book_guest_pass({"email": "a@b.com"}, info)
        self.assertTrue(result["success"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["email"], "a@b.com")
        self.assertTrue(info["guest_pass_booked"])
        self.assertGreater(info["protect_playback_until"], time.monotonic())


class PlaybackAndPromptTests(unittest.TestCase):
    def test_confirmation_playback_is_protected_then_expires(self):
        info = _stream()
        protect_guest_pass_playback(info, seconds=30)
        self.assertTrue(_playback_protected(info))
        info["protect_playback_until"] = time.monotonic() - 1
        self.assertFalse(_playback_protected(info))

    def test_phone_session_transcribes_the_caller(self):
        session = _session_config("test", allow_interrupt=True)
        self.assertEqual(
            session["audio"]["input"]["transcription"]["model"],
            "gpt-4o-transcribe",
        )

    def test_transcript_event_text_is_read(self):
        event = SimpleNamespace(transcript="yes please")
        self.assertEqual(_event_transcript(event), "yes please")

    def test_prompt_and_tool_wait_for_a_yes(self):
        agent = {"tools": {"book_guest_pass": True}}
        policy = tool_source_policy(agent)
        self.assertIn("clearly say yes", policy)
        self.assertIn("one sentence", policy)
        description = next(tool["description"] for tool in OPENAI_TOOLS if tool["name"] == "book_guest_pass")
        self.assertIn("clearly agreed", description)
        self.assertIn("not linked to Mindbody", description)


if __name__ == "__main__":
    unittest.main()
