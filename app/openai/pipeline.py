"""V1 phone pipeline: OpenAI STT → Chat Completions (+ tools) → TTS (EL or OpenAI)."""

from __future__ import annotations

import asyncio
import json
import time
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
from app.openai.stt import is_actionable_transcript, transcribe_mulaw
from app.tools.chat_format import to_chat_completion_tools
from app.tools.dispatcher import (
    dispatch_tool,
    note_caller_transcript,
    note_caller_turn_after_offer,
    note_guest_pass_offer,
    parse_tool_arguments,
)
from app.tools.end_call import end_call


def _chat_completion_limits(model: str, n: int) -> dict[str, Any]:
    """Token/reasoning kwargs for Chat Completions.

    gpt-5 / o-series reject max_tokens. Their max_completion_tokens budget also
    covers hidden reasoning tokens — a tight cap (e.g. 180) often yields empty
    content with finish_reason=length. Use a larger cap + minimal reasoning.
    """
    m = (model or "").lower()
    if m.startswith(("gpt-5", "o1", "o3", "o4")):
        return {
            "max_completion_tokens": max(n, 1024),
            "reasoning_effort": "minimal",
        }
    return {"max_tokens": n}


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
        language = resolve_agent_language(agent)
        from app.core.realtime import AGENT_LANGUAGES

        language_name = AGENT_LANGUAGES.get(language, "English")
        system = (
            f"{(system or 'You are a helpful phone agent.').rstrip()}\n\n"
            "# LIVE TURN RULES\n"
            f"Reply in {language_name} ({language}) only. Do not mirror the caller's language.\n"
            "Understand Hindi/English/Hinglish — never ask the caller to speak English.\n"
            "Never invent the caller's name, email, or details from unclear audio.\n"
            "If CRM already has first/last name or email, use those; only ask for missing fields.\n"
            "Keep each reply to 1-2 short phone sentences. Prefer speed over long explanations.\n"
            "The caller's words may arrive with STT errors — infer intent from context, "
            "and ask one short clarification only when needed.\n"
        )
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system}
        ]
        self.model = resolve_chat_model(agent)
        self.language = language
        self.language_name = language_name
        tts = "ElevenLabs" if uses_elevenlabs_voice(agent) else "OpenAI Speech"
        print(
            "🧠 V1 pipeline ready — STT +",
            self.model,
            "+",
            tts,
            f"— speak {self.language_name} ({self.language})",
            f"— stt={OPENAI_STT_MODEL}",
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
                **_chat_completion_limits(self.model, 120),
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
        t0 = time.perf_counter()
        try:
            # Auto-detect caller speech (Hindi/English/mixed). Agent reply language is separate.
            text = await transcribe_mulaw(
                mulaw,
                language=None,
                model=OPENAI_STT_MODEL,
            )
            stt_ms = (time.perf_counter() - t0) * 1000
            if not is_actionable_transcript(text):
                print(f"🎤 STT empty — ignore ({stt_ms:.0f}ms)")
                return
            print(f"🎤 STT ({stt_ms:.0f}ms):", text)
            # New caller turn — allow hangup again after a prior book turn.
            self.stream_info.pop("block_end_call_after_book", None)
            note_caller_transcript(self.stream_info, text)
            note_caller_turn_after_offer(self.stream_info)
            self.messages.append({"role": "user", "content": text})

            t1 = time.perf_counter()
            reply = await self._chat_with_tools()
            chat_ms = (time.perf_counter() - t1) * 1000
            if not reply:
                reply = "Sorry, I missed that. Could you say that again?"
            if self.language != "en":
                reply = await self._rewrite_in_agent_language(reply)
            print(f"🗣️ Pipeline reply ({chat_ms:.0f}ms chat):", reply[:200])
            note_guest_pass_offer(self.stream_info, reply)
            self.messages.append({"role": "assistant", "content": reply})

            t2 = time.perf_counter()
            await self.speak(self.ws, self.stream_info, reply)
            tts_ms = (time.perf_counter() - t2) * 1000
            total_ms = (time.perf_counter() - t0) * 1000
            print(
                f"⏱️ Turn latency — stt={stt_ms:.0f}ms chat={chat_ms:.0f}ms "
                f"tts={tts_ms:.0f}ms total={total_ms:.0f}ms"
            )

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
            resolve_agent_tools(
                self.stream_info.get("agent") or {},
                tools_enabled=True,
                member=self.stream_info.get("member") or {},
            )
        )
        for _ in range(6):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": self.messages,
                # Keep phone replies short via prompt; leave headroom for reasoning models.
                **_chat_completion_limits(self.model, 180),
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
            if not content and not tool_calls:
                finish = getattr(choice, "finish_reason", None)
                usage = getattr(response, "usage", None)
                details = getattr(usage, "completion_tokens_details", None) if usage else None
                reasoning_n = getattr(details, "reasoning_tokens", None) if details else None
                print(
                    "⚠️ Empty chat content —",
                    f"finish={finish}",
                    f"reasoning_tokens={reasoning_n}",
                    f"completion_tokens={getattr(usage, 'completion_tokens', None)}",
                )

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
                # Book before hangup so a failed book can block end_call in the same turn.
                ordered = sorted(
                    tool_calls,
                    key=lambda tc: (
                        0
                        if tc.function.name in ("book_guest_pass", "book_class_visit")
                        else (2 if tc.function.name == "end_call" else 1)
                    ),
                )
                for tc in ordered:
                    name = tc.function.name
                    args = parse_tool_arguments(tc.function.arguments or "{}")
                    print("🔧", name, args)
                    if name == "book_guest_pass":
                        await self.speak(
                            self.ws,
                            self.stream_info,
                            "Okay — I'm adding the 7 Day Guest Pass to your profile now. One moment.",
                        )
                    elif name == "book_class_visit":
                        await self.speak(
                            self.ws,
                            self.stream_info,
                            "Okay — I'm booking that class visit now. One moment.",
                        )
                    result = await dispatch_tool(name, args, self.stream_info)
                    print("🔧 result:", result)
                    if name in ("book_guest_pass", "book_class_visit"):
                        self.stream_info["block_end_call_after_book"] = True
                        self.stream_info["end_call_requested"] = False
                        self.stream_info["hangup_after_goodbye"] = False
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
                    if name == "book_guest_pass" and (result or {}).get("success"):
                        self.messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "The 7 Day Guest Pass is now on this caller's account. "
                                    "Do not offer or book another. If they ask about class packages "
                                    "or membership, answer from CRM Membership / Class packs."
                                ),
                            }
                        )
                continue

            return content
        return ""
