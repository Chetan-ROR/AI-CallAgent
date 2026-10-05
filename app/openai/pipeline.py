"""V1 phone pipeline: OpenAI STT → Chat Completions (+ tools) → TTS (EL or OpenAI)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable

from openai import AsyncOpenAI

from app.audio.vad import UtteranceVad
from app.core.config import OPENAI_API_KEY, OPENAI_STT_MODEL
from app.core.prompt_builder import (
    agent_spoken_opening,
    build_instructions,
)
from app.core.realtime import (
    agent_allow_interrupt,
    resolve_agent_language,
    resolve_agent_tools,
    resolve_chat_model,
    uses_elevenlabs_voice,
)
from app.openai.stt import transcribe_mulaw
from app.tools.chat_format import to_chat_completion_tools
from app.tools.dispatcher import (
    dispatch_tool,
    note_caller_transcript,
    note_caller_turn_after_offer,
    note_guest_pass_offer,
    parse_tool_arguments,
)
from app.tools.end_call import end_call


class PipelineSession:
    def __init__(
        self,
        *,
        ws,
        stream_info: dict,
        speak: Callable[..., Awaitable[bool]],
        cancel_tts: Callable[..., Awaitable[None]],
        twilio_clear: Callable[..., Awaitable[None]],
        playback_protected: Callable[[dict], bool],
        goodbye_delay: Callable[[str], float],
        is_goodbye: Callable[[str], bool],
    ):
        self.ws = ws
        self.stream_info = stream_info
        self.speak = speak
        self.cancel_tts = cancel_tts
        self.twilio_clear = twilio_clear
        self.playback_protected = playback_protected
        self.goodbye_delay = goodbye_delay
        self.is_goodbye = is_goodbye
        self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self.vad = UtteranceVad()
        self.busy = False
        self._turn_lock = asyncio.Lock()
        agent = stream_info.get("agent") or {}
        system = build_instructions(
            stream_info.get("member") or {},
            stream_info.get("studio") or {},
            agent,
            direction=stream_info.get("direction"),
        )
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system or "You are a helpful phone agent."}
        ]
        self.model = resolve_chat_model(agent)
        self.language = resolve_agent_language(agent)
        from app.core.realtime import AGENT_LANGUAGES

        self.language_name = AGENT_LANGUAGES.get(self.language, "English")
        tts = "ElevenLabs" if uses_elevenlabs_voice(agent) else "OpenAI Speech"
        print(
            "🧠 V1 pipeline ready — STT +",
            self.model,
            "+",
            tts,
            f"— speak {self.language_name} ({self.language})",
        )

    async def _rewrite_in_agent_language(self, text: str) -> str:
        """Ensure greeting/reply text matches agent language before TTS."""
        spoken = (text or "").strip()
        if not spoken or self.language == "en":
            return spoken
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Rewrite the phone line below into {self.language_name} only. "
                            "Keep the same meaning and keep it short for a phone greeting/reply. "
                            f"Output ONLY the {self.language_name} text — no quotes, no English."
                        ),
                    },
                    {"role": "user", "content": spoken},
                ],
            )
            rewritten = (
                ((response.choices or [None])[0].message.content or "").strip()
                if response.choices
                else ""
            )
            return rewritten or spoken
        except Exception as exc:
            print("⚠️ Language rewrite failed:", repr(exc))
            return spoken

    async def start_greeting(self) -> None:
        agent = self.stream_info.get("agent") or {}
        opening = agent_spoken_opening(
            agent,
            self.stream_info.get("member"),
            self.stream_info.get("studio"),
            direction=self.stream_info.get("direction"),
        )
        if not opening:
            opening = "Hi, thanks for taking my call — do you have a quick minute?"
        if self.language != "en":
            opening = await self._rewrite_in_agent_language(opening)
        self.messages.append({"role": "assistant", "content": opening})
        print("🤖 Pipeline greeting:", opening[:160])
        ok = await self.speak(self.ws, self.stream_info, opening)
        self.stream_info["greeting_done"] = True
        self.stream_info["audio_sent"] = False
        if ok:
            print("✅ Greeting finished (pipeline), now listening")
        else:
            print("⚠️ Greeting TTS failed — still marking greeting_done")

    async def on_media_payload(self, payload_b64: str) -> None:
        if not self.stream_info.get("greeting_done"):
            return
        if self.busy:
            # Barge-in while TTS plays (speak sets tts_playing).
            if (
                agent_allow_interrupt(self.stream_info.get("agent"))
                and not self.playback_protected(self.stream_info)
                and self.stream_info.get("tts_playing")
            ):
                utterance = self.vad.push_mulaw_b64(payload_b64)
                if self.vad.speaking or utterance:
                    await self.cancel_tts(self.stream_info)
                    await self.twilio_clear(self.ws, self.stream_info)
                    self.busy = False
                    if utterance:
                        asyncio.create_task(self._handle_utterance(utterance))
                return
            return

        utterance = self.vad.push_mulaw_b64(payload_b64)
        if utterance:
            asyncio.create_task(self._handle_utterance(utterance))

    async def _handle_utterance(self, mulaw: bytes) -> None:
        async with self._turn_lock:
            await self._run_turn(mulaw)

    async def _run_turn(self, mulaw: bytes) -> None:
        self.busy = True
        try:
            # Auto-detect caller speech; agent reply language is enforced separately.
            text = await transcribe_mulaw(
                mulaw,
                language=None,
                model=OPENAI_STT_MODEL,
            )
            if not text:
                print("🎤 STT empty — ignore")
                return
            print("🎤 STT:", text)
            note_caller_transcript(self.stream_info, text)
            note_caller_turn_after_offer(self.stream_info)
            self.messages.append({"role": "user", "content": text})
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Reply to the caller in {self.language_name} ({self.language}) only. "
                        "Do not mirror their language."
                    ),
                }
            )

            reply = await self._chat_with_tools()
            if not reply:
                reply = "Sorry, I missed that. Could you say that again?"
            if self.language != "en":
                reply = await self._rewrite_in_agent_language(reply)
            print("🗣️ Pipeline reply:", reply[:200])
            note_guest_pass_offer(self.stream_info, reply)
            self.messages.append({"role": "assistant", "content": reply})

            await self.speak(self.ws, self.stream_info, reply)

            if self.stream_info.get("hangup_after_goodbye") and self.is_goodbye(reply):
                delay = self.goodbye_delay(reply)
                print(f"📞 Pipeline goodbye — wait {delay:.1f}s then hangup")
                await asyncio.sleep(delay)
                call_sid = self.stream_info.get("call_sid")
                if call_sid:
                    await end_call(call_sid)
                self.stream_info["hangup_after_goodbye"] = False
                self.stream_info["end_call_requested"] = False
        except Exception as exc:
            print("❌ Pipeline turn failed:", repr(exc))
        finally:
            self.busy = False

    async def _chat_with_tools(self) -> str:
        tools = to_chat_completion_tools(
            resolve_agent_tools(self.stream_info.get("agent") or {}, tools_enabled=True)
        )
        for _ in range(6):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": self.messages,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            response = await self.client.chat.completions.create(**kwargs)
            choice = (response.choices or [None])[0]
            if choice is None:
                return ""
            message = choice.message
            tool_calls = getattr(message, "tool_calls", None) or []
            content = (message.content or "").strip()

            if tool_calls:
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments or "{}",
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )
                for tc in tool_calls:
                    name = tc.function.name
                    args = parse_tool_arguments(tc.function.arguments or "{}")
                    print("🔧", name, args)
                    result = await dispatch_tool(name, args, self.stream_info)
                    print("🔧 result:", result)
                    if name == "end_call" and (result or {}).get("success"):
                        self.stream_info["end_call_requested"] = True
                        self.stream_info["hangup_after_goodbye"] = True
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, default=str),
                        }
                    )
                continue

            return content
        return ""
