"""Practice call uses ElevenLabs TTS, not an OpenAI Realtime voice id."""

import unittest

from app.core.realtime import (
    DEFAULT_VOICE,
    build_realtime_session,
    resolve_practice_tts,
)


class PracticeTtsTests(unittest.TestCase):
    def test_elevenlabs_agent_uses_saved_voice_id(self):
        agent = {
            "voice_settings": {
                "provider": "elevenlabs",
                "voice": "H538pP1BbhodCGiYVMKD",
                "model": "gpt-4.1",
            }
        }
        provider, voice = resolve_practice_tts(agent)
        self.assertEqual(provider, "elevenlabs")
        self.assertEqual(voice, "H538pP1BbhodCGiYVMKD")

    def test_override_elevenlabs_id_without_agent(self):
        provider, voice = resolve_practice_tts(None, override="H538pP1BbhodCGiYVMKD")
        self.assertEqual(provider, "elevenlabs")
        self.assertEqual(voice, "H538pP1BbhodCGiYVMKD")

    def test_elevenlabs_id_wins_over_default_openai_provider(self):
        agent = {
            "voice_settings": {
                "provider": "openai",
                "voice": "H538pP1BbhodCGiYVMKD",
            }
        }
        provider, voice = resolve_practice_tts(agent)
        self.assertEqual(provider, "elevenlabs")
        self.assertEqual(voice, "H538pP1BbhodCGiYVMKD")

    def test_openai_agent_keeps_preset(self):
        agent = {"voice_settings": {"provider": "openai", "voice": "coral"}}
        provider, voice = resolve_practice_tts(agent)
        self.assertEqual(provider, "openai")
        self.assertEqual(voice, "coral")

    def test_practice_session_is_text_only_for_elevenlabs(self):
        session = build_realtime_session(
            "practice",
            instructions="hi",
            first_message="hello",
            agent={"voice_settings": {"provider": "elevenlabs", "voice": "H538pP1BbhodCGiYVMKD"}},
            speak_via_realtime=False,
        )
        self.assertEqual(session["output_modalities"], ["text"])
        self.assertNotIn("output", session["audio"])

    def test_practice_session_speaks_openai_voice(self):
        session = build_realtime_session(
            "practice",
            voice="marin",
            speak_via_realtime=True,
        )
        self.assertEqual(session["output_modalities"], ["audio"])
        self.assertEqual(session["audio"]["output"]["voice"], DEFAULT_VOICE)


if __name__ == "__main__":
    unittest.main()
