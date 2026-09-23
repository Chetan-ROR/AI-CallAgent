from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.config import LLC_CALL_AGENT_KEY, LLC_CLIENT_ID
from app.core.prompt_builder import (
    build_instructions,
    callback_instructions,
    greeting_instructions,
    is_known_member,
    member_display_first_name,
    offer_followup_instructions,
    use_agent_prompt_only,
    waiting_instructions,
)
from app.llc.client import LlcClient, get_agent_prefetch, get_crm_prefetch
from app.tools.definitions import OPENAI_TOOLS
from app.tools.dispatcher import dispatch_tool, parse_tool_arguments
from app.tools.end_call import end_call
from app.twilio.call_tracking import mark_media_stream_started

import asyncio
import os
import json
import socket
import time

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

REALTIME_WS_OPTIONS = {
    "open_timeout": 45,
    "family": socket.AF_INET,
    "ping_interval": 10,
    "ping_timeout": 20,
}

QUIET_EVENTS = {
    "response.output_audio.delta",
    "response.output_audio_transcript.delta",
    "response.output_audio.done",
    "response.output_audio_transcript.done",
    "response.content_part.added",
    "response.content_part.done",
    "response.output_item.added",
    "response.output_item.done",
    "conversation.item.added",
    "conversation.item.done",
    "rate_limits.updated",
    "input_audio_buffer.committed",
    "response.function_call_arguments.delta",
}

router = APIRouter()


def _clean(value):
    if value in (None, "", "None", "null"):
        return None
    return value


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0", "false", "False"):
        return False
    return True


def _agent_tools(stream_info: dict | None, *, tools_enabled: bool):
    if not tools_enabled:
        return []
    agent = (stream_info or {}).get("agent") or {}
    enabled = agent.get("tools")
    if not isinstance(enabled, dict) or not enabled:
        return OPENAI_TOOLS
    selected = [
        tool
        for tool in OPENAI_TOOLS
        if _truthy(enabled.get(tool.get("name")))
    ]
    return selected or OPENAI_TOOLS


VALID_VOICES = {
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
}


def _session_config(
    instructions: str,
    *,
    allow_interrupt: bool,
    create_response: bool = True,
    tools_enabled: bool = True,
    stream_info: dict | None = None,
):
    voice = (((stream_info or {}).get("agent") or {}).get("voice_settings") or {})
    voice_name = str(voice.get("voice") or "alloy").strip().lower()
    if voice_name not in VALID_VOICES:
        voice_name = "alloy"
    if voice.get("allow_interrupt") is False:
        allow_interrupt = False

    return {
        "type": "realtime",
        "instructions": instructions,
        "tools": _agent_tools(stream_info, tools_enabled=tools_enabled),
        "tool_choice": "auto" if tools_enabled else "none",
        "output_modalities": ["audio"],
        # Realtime audio turns need headroom; 380 caused empty incomplete responses after barge-in.
        "max_output_tokens": 1024 if not tools_enabled else 1200,
        "audio": {
            "input": {
                "format": {
                    "type": "audio/pcmu"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": create_response,
                    "interrupt_response": allow_interrupt,
                    "threshold": 0.65 if allow_interrupt else 0.9,
                    "silence_duration_ms": 900,
                }
            },
            "output": {
                "format": {
                    "type": "audio/pcmu"
                },
                "voice": voice_name,
            }
        }
    }


def _dump(obj, limit=2000):
    if obj is None:
        return None
    for name in ("model_dump", "to_dict"):
        method = getattr(obj, name, None)
        if callable(method):
            try:
                return json.dumps(method(), default=str)[:limit]
            except Exception:
                pass
    try:
        return json.dumps(obj, default=str)[:limit]
    except Exception:
        return repr(obj)[:limit]


def _response_status(event):
    response = getattr(event, "response", None) or event
    status = getattr(response, "status", None) or getattr(event, "status", None)
    details = getattr(response, "status_details", None)
    error = getattr(details, "error", None) if details is not None else None
    if error is None:
        error = getattr(response, "error", None)
    reason = getattr(details, "reason", None) if details is not None else None
    detail_type = getattr(details, "type", None) if details is not None else None
    return {
        "status": status,
        "reason": reason,
        "detail_type": detail_type,
        "error": _dump(error, 800) if error is not None else None,
        "details": _dump(details, 800) if details is not None else None,
    }


async def _twilio_clear(ws, stream_info):
    sid = stream_info.get("sid")
    if not sid:
        return
    try:
        await ws.send_json({"event": "clear", "streamSid": sid})
        print("🔇 Twilio playback cleared")
    except Exception as exc:
        print("⚠️ Twilio clear failed:", repr(exc))


async def _cancel_active_response(openai, stream_info):
    if not stream_info.get("response_active"):
        return
    try:
        await openai.response.cancel()
        print("🛑 OpenAI response cancelled (barge-in)")
    except Exception as exc:
        stream_info["response_active"] = False
        msg = repr(exc)
        if "response_cancel_not_active" not in msg and "no active response" not in msg.lower():
            print("⚠️ response.cancel:", msg)


async def _cancel_task(task):
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def load_crm_context(stream_info: dict, *, force: bool = False):
    client_id = stream_info.get("client_id")
    member_id = stream_info.get("member_id")

    cached = None if force else get_crm_prefetch(client_id, member_id)
    if cached:
        result = cached
        print("👤 Using CRM fetched before the call")
    else:
        llc = LlcClient()
        if not llc.enabled:
            print("⚠️ LLC API is not configured")
            stream_info["crm_loaded"] = True
            return

        result = await llc.lookup_member(
            client_id=client_id,
            member_id=member_id,
        )
        print("👤 LLC lookup:", json.dumps(result, default=str)[:1000])

    studio = result.get("studio") or {}
    stream_info["studio"] = studio
    stream_info["client_id"] = studio.get("id") or stream_info.get("client_id")

    member = result.get("member") or {}
    if member.get("id"):
        stream_info["member"] = member
        stream_info["member_id"] = member.get("id") or stream_info.get("member_id")
        print(
            "👤 CRM member on stream:",
            member.get("first_name") or member.get("full_name"),
            stream_info["member_id"],
        )
    elif member_id:
        print("⚠️ member_id on call but CRM member payload is empty:", member_id)
    else:
        print("📞 No member on this call — using studio classes and plans only")

    stream_info["crm_loaded"] = True


async def load_agent_context(stream_info: dict):
    client_id = stream_info.get("client_id")
    agent_id = stream_info.get("agent_id")

    cached = get_agent_prefetch(client_id, agent_id)
    if cached:
        result = cached
        print("🤖 Using agent fetched before the call")
    else:
        llc = LlcClient()
        if not llc.enabled or not client_id:
            print("⚠️ No LLC agent for this call — using static prompt")
            stream_info["agent"] = stream_info.get("agent") or {}
            return
        result = await llc.lookup_agent(
            client_id=client_id,
            agent_id=agent_id,
            agent_key=None if agent_id else LLC_CALL_AGENT_KEY,
        )
        print("🤖 LLC agent lookup:", json.dumps(result, default=str)[:1000])

    agent = (result or {}).get("agent") or {}
    if (result or {}).get("success") and agent.get("id"):
        stream_info["agent"] = agent
        stream_info["agent_id"] = agent.get("id")
        print("🤖 Call agent:", agent.get("name"), agent.get("id"))
        if (agent.get("conversation_prompt") or "").strip():
            print("📝 Using LLC agent conversation_prompt on this call (no external sales scripts)")
        else:
            print("📝 No agent conversation_prompt — using code fallback prompt")
        if (agent.get("first_message") or "").strip():
            print("📝 Using LLC agent first_message for greeting")
    else:
        stream_info["agent"] = stream_info.get("agent") or {}


async def refresh_crm_instructions(openai, stream_info):
    try:
        await load_crm_context(stream_info)
        print("👤 CRM lookup finished, waiting for them to answer before pitching")
    except Exception as exc:
        print("❌ CRM context update failed:", repr(exc))


async def enable_conversation_listen(openai, stream_info):
    try:
        await openai.session.update(
            session=_session_config(
                build_instructions(
                    stream_info.get("member"),
                    stream_info.get("studio"),
                    stream_info.get("agent"),
                ),
                allow_interrupt=True,
                create_response=True,
                tools_enabled=True,
                stream_info=stream_info,
            )
        )
        print("✅ Listening after offer")
    except Exception as exc:
        print("❌ Failed to enable conversation listen:", repr(exc))


async def enable_listening_session(openai, stream_info):
    try:
        if not stream_info.get("crm_loaded"):
            await load_crm_context(stream_info)
        elif stream_info.get("member_id") and not (stream_info.get("member") or {}).get("id"):
            await load_crm_context(stream_info, force=True)
        member = stream_info.get("member")
        if is_known_member(member):
            print("👤 Known member listening script:", member_display_first_name(member))
        agent = stream_info.get("agent")
        if use_agent_prompt_only(agent):
            listen_instructions = (
                build_instructions(
                    stream_info.get("member"),
                    stream_info.get("studio"),
                    agent,
                )
                + "\n\n# NOW\nThe customer is responding after your greeting. "
                "Follow your agent instructions. One or two short sentences, then wait."
            )
        else:
            listen_instructions = waiting_instructions(
                agent,
                stream_info.get("studio"),
                stream_info.get("member"),
            )
        agent_only = use_agent_prompt_only(agent)
        await openai.session.update(
            session=_session_config(
                listen_instructions,
                allow_interrupt=True,
                create_response=True,
                tools_enabled=agent_only,
                stream_info=stream_info,
            )
        )
        if agent_only:
            # Full LLC prompt + CRM already loaded — avoid a second session.update from sales routing.
            stream_info["pitch_unlocked"] = True
        print("✅ Listening for their answer")
    except Exception as exc:
        print("❌ Failed to enable listening:", repr(exc))


async def enable_callback_session(openai, stream_info):
    if stream_info.get("pitch_unlocked"):
        return
    stream_info["pitch_unlocked"] = True
    stream_info["callback_mode"] = True
    try:
        await openai.session.update(
            session=_session_config(
                callback_instructions(stream_info.get("agent")),
                allow_interrupt=True,
                create_response=True,
                tools_enabled=True,
                stream_info=stream_info,
            )
        )
        print("✅ Callback mode — wait for a time, then hang up")
    except Exception as exc:
        print("❌ Failed to enable callback mode:", repr(exc))
        stream_info["pitch_unlocked"] = False


def _goodbye_playback_delay(spoken: str) -> float:
    words = len((spoken or "").split())
    # Realtime audio + Twilio buffer needs more tail than model "done" event.
    return min(14.0, max(4.0, words * 0.55 + 2.0))


def _is_goodbye_spoken(spoken: str) -> bool:
    text = (spoken or "").lower()
    if len(text) < 12:
        return False
    return any(
        phrase in text
        for phrase in (
            "thank",
            "thanks for your time",
            "have a good one",
            "reach out",
            "follow up",
        )
    )


def _route_first_reply(spoken: str) -> str:
    text = (spoken or "").lower()
    if "awesome" in text or "profile with us" in text or "free trainer consult" in text:
        return "sales"
    if "sixty dollars" in text or "$60" in text or "sixty dollar" in text:
        return "sales"
    if "offer at total bizz" in text or "off the monthly plan" in text:
        return "sales"
    if "better time" in text or "call you back" in text or "call back" in text:
        return "callback"
    if "no problem" in text and "let you go" in text:
        return "hangup"
    if "let you go" in text and "awesome" not in text and "offer" not in text:
        return "hangup"
    if "have a good" in text and "offer" not in text:
        return "hangup"
    if "didn't catch" in text or "is now okay" in text:
        return "wait"
    return "callback"


async def unlock_full_conversation(openai, stream_info):
    if stream_info.get("pitch_unlocked"):
        return
    stream_info["pitch_unlocked"] = True
    try:
        for _ in range(40):
            if stream_info.get("crm_loaded"):
                break
            await asyncio.sleep(0.05)
        if not stream_info.get("crm_loaded"):
            await load_crm_context(stream_info)
        agent = stream_info.get("agent")
        if use_agent_prompt_only(agent):
            print("✅ LLC agent mode — full prompt + CRM (no forced offer follow-up)")
            await openai.session.update(
                session=_session_config(
                    build_instructions(
                        stream_info.get("member"),
                        stream_info.get("studio"),
                        agent,
                    ),
                    allow_interrupt=True,
                    create_response=True,
                    tools_enabled=True,
                    stream_info=stream_info,
                )
            )
            return
        await openai.session.update(
            session=_session_config(
                build_instructions(
                    stream_info.get("member"),
                    stream_info.get("studio"),
                    agent,
                ),
                allow_interrupt=True,
                create_response=False,
                tools_enabled=True,
                stream_info=stream_info,
            )
        )
        if stream_info.get("end_call_requested") or stream_info.get("callback_mode"):
            return
        print("✅ Offer intro done — pause then classes/memberships question")
        await asyncio.sleep(2)
        if stream_info.get("end_call_requested") or stream_info.get("callback_mode"):
            return
        await openai.response.create(
            response={
                "output_modalities": ["audio"],
                "instructions": offer_followup_instructions(
                    stream_info.get("studio"),
                    agent,
                    stream_info.get("member"),
                ),
            }
        )
        stream_info["offer_pending"] = True
    except Exception as exc:
        print("❌ Failed to unlock conversation:", repr(exc))
        stream_info["pitch_unlocked"] = False


async def _create_followup_response(openai, stream_info):
    stream_info["awaiting_tool_speech"] = True
    if stream_info.get("response_active"):
        await _cancel_active_response(openai, stream_info)
        await asyncio.sleep(0.08)
    try:
        await openai.response.create(
            response={"output_modalities": ["audio"]}
        )
    except Exception as exc:
        stream_info["awaiting_tool_speech"] = False
        print("⚠️ response.create after tool:", repr(exc))


async def receive_from_openai(live, ws, stream_info):

    function_calls = {}
    transcript = []
    openai = live.openai

    try:
        while True:
            kind, payload = await live.events.get()
            if kind != "event":
                print("⚠️ OpenAI event stream ended")
                break

            event = payload
            event_type = event.type

            if event_type not in QUIET_EVENTS:
                print("EVENT:", event_type)

            if event_type == "response.created":
                stream_info["response_active"] = True

            elif event_type == "input_audio_buffer.speech_started":
                stream_info["user_speaking"] = True
                if stream_info.get("greeting_done") and not stream_info.get("awaiting_tool_speech"):
                    await _twilio_clear(ws, stream_info)
                    await _cancel_active_response(openai, stream_info)

            elif event_type == "input_audio_buffer.speech_stopped":
                stream_info["user_speaking"] = False

            elif event_type == "response.output_audio.delta":

                stream_info["audio_sent"] = True

                if stream_info["sid"]:
                    await ws.send_json(
                        {
                            "event": "media",
                            "streamSid": stream_info["sid"],
                            "media": {
                                "payload": event.delta
                            }
                        }
                    )

            elif event_type == "response.output_audio_transcript.delta":

                transcript.append(event.delta)

            elif event_type == "response.function_call_arguments.delta":

                call_id = event.call_id

                if call_id not in function_calls:
                    function_calls[call_id] = ""

                function_calls[call_id] += event.delta

            elif event_type == "response.function_call_arguments.done":

                call_id = event.call_id
                function_name = event.name
                arguments = parse_tool_arguments(
                    getattr(event, "arguments", None) or function_calls.get(call_id, "{}")
                )

                print("🔧", function_name, arguments)

                result = await dispatch_tool(function_name, arguments, stream_info)
                print("🔧 result:", result)

                if function_name == "end_call" and result.get("success"):
                    stream_info["end_call_requested"] = True
                    stream_info["hangup_after_goodbye"] = True

                await openai.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result, default=str),
                    }
                )
                print("🔧 function_call_output sent")

                await _create_followup_response(openai, stream_info)
                print("🔧 response.create after tool")

                function_calls.pop(call_id, None)

            elif event_type == "response.done":

                stream_info["response_active"] = False
                stream_info["awaiting_tool_speech"] = False
                info = _response_status(event)
                spoken = "".join(transcript).strip()
                transcript = []
                print("RESPONSE DONE:", info["status"], "reason=", info["reason"], "type=", info["detail_type"])
                if info["error"]:
                    print("❌ RESPONSE ERROR:", info["error"])
                elif info["details"] and info["status"] not in ("completed", None):
                    print("RESPONSE DETAILS:", info["details"])

                if spoken:
                    print("🗣️", spoken)
                    stream_info["failed_recovery"] = False
                elif info["status"] == "cancelled":
                    print("⚠️ AI speech cancelled (barge-in or new response)")
                elif info["status"] == "failed" or (
                    info["status"] == "incomplete"
                    and info["reason"] in ("max_output_tokens", "content_filter")
                ):
                    print("⚠️ AI turn failed or truncated:", info["reason"] or info["status"])
                    if stream_info.get("greeting_done") and not stream_info.get("failed_recovery"):
                        stream_info["failed_recovery"] = True
                        try:
                            await openai.response.create(
                                response={
                                    "output_modalities": ["audio"],
                                    "instructions": (
                                        "Say one short sentence: Sorry, I missed that. "
                                        "Could you say that again? Then stop."
                                    ),
                                }
                            )
                        except Exception as exc:
                            print("⚠️ failed-turn recovery:", repr(exc))
                else:
                    print("⚠️ Empty AI turn (no speech)")

                if stream_info.get("hangup_after_goodbye") and info["status"] in (
                    "completed",
                    "incomplete",
                ):
                    if not _is_goodbye_spoken(spoken):
                        print("📞 end_call tool used — waiting for goodbye speech before hangup")
                    else:
                        delay = _goodbye_playback_delay(spoken)
                        print(
                            f"📞 Goodbye sent ({len(spoken.split())} words) — "
                            f"waiting {delay:.1f}s for playback, then hangup"
                        )
                        await asyncio.sleep(delay)
                        call_sid = stream_info.get("call_sid")
                        if call_sid:
                            await end_call(call_sid)
                        stream_info["end_call_requested"] = False
                        stream_info["hangup_after_goodbye"] = False
                        return

                if stream_info.get("audio_sent") and not stream_info.get("greeting_done"):
                    stream_info["greeting_done"] = True
                    stream_info["audio_sent"] = False
                    print("✅ Greeting finished, now listening")
                    asyncio.create_task(enable_listening_session(openai, stream_info))

                elif stream_info.get("offer_pending") and info["status"] != "cancelled":
                    stream_info["offer_pending"] = False
                    print("✅ Offer finished, now answering questions")
                    asyncio.create_task(enable_conversation_listen(openai, stream_info))

                elif (
                    stream_info.get("greeting_done")
                    and spoken
                    and not stream_info.get("pitch_unlocked")
                    and info["status"] != "cancelled"
                ):
                    route = _route_first_reply(spoken)
                    print("🧭 First reply route:", route)
                    if route == "sales":
                        asyncio.create_task(unlock_full_conversation(openai, stream_info))
                    elif route == "callback":
                        asyncio.create_task(enable_callback_session(openai, stream_info))
                    elif route == "hangup":
                        print("📞 Busy/no — hanging up after goodbye")
                        await asyncio.sleep(1.2)
                        call_sid = stream_info.get("call_sid")
                        if call_sid:
                            await end_call(call_sid)
                        return

            elif event_type == "error":
                error = getattr(event, "error", None)
                code = getattr(error, "code", None) if error else None
                if code == "response_cancel_not_active":
                    stream_info["response_active"] = False
                else:
                    print("❌ OPENAI ERROR:", _dump(event, 2000))
                    print(
                        "❌ OPENAI ERROR DETAIL:",
                        _dump(error, 1200) if error is not None else None,
                    )
                if code == "conversation_already_has_active_response":
                    stream_info["response_active"] = True

    except Exception as e:

        print("❌ OPENAI LISTENER ERROR:", repr(e))


class RealtimeSession:
    def __init__(self, connection, openai):
        self.connection = connection
        self.openai = openai
        self.events = asyncio.Queue()
        self.closed = False
        self._pump = asyncio.create_task(self._run_pump())

    @property
    def alive(self):
        return not self.closed and not self._pump.done()

    async def _run_pump(self):
        try:
            async for event in self.openai:
                await self.events.put(("event", event))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("⚠️ OpenAI pool pump ended:", repr(exc))
        finally:
            self.closed = True
            await self.events.put(("end", None))

    async def close(self):
        self.closed = True
        self._pump.cancel()
        try:
            await self._pump
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await self.connection.__aexit__(None, None, None)
        except Exception:
            pass


_pool_lock = asyncio.Lock()
_pool_session: RealtimeSession | None = None
_pool_opening: asyncio.Task | None = None


async def _create_live_session():
    started = time.time()
    print("🔌 Opening OpenAI Realtime...")
    connection = client.realtime.connect(
        model="gpt-realtime-2",
        websocket_connection_options=REALTIME_WS_OPTIONS,
    )
    openai = await connection.__aenter__()
    live = RealtimeSession(connection, openai)
    try:
        await openai.session.update(
            session=_session_config(
                greeting_instructions(),
                allow_interrupt=False,
                create_response=False,
                tools_enabled=False,
            )
        )
    except Exception:
        await live.close()
        raise
    print(f"✅ OpenAI Realtime ready ({time.time() - started:.1f}s)")
    return live


async def ensure_openai_ready():
    global _pool_session, _pool_opening

    async with _pool_lock:
        if _pool_session and _pool_session.alive:
            return _pool_session

        if _pool_session:
            asyncio.create_task(_pool_session.close())
            _pool_session = None

        if _pool_opening is None or _pool_opening.done():
            _pool_opening = asyncio.create_task(_create_live_session())
        opening = _pool_opening

    try:
        live = await asyncio.wait_for(asyncio.shield(opening), timeout=90.0)
    except asyncio.TimeoutError as exc:
        async with _pool_lock:
            if opening is _pool_opening:
                _pool_opening = None
        raise TimeoutError("OpenAI Realtime handshake timed out after 90s") from exc
    except Exception:
        async with _pool_lock:
            if opening is _pool_opening:
                _pool_opening = None
        raise

    async with _pool_lock:
        if opening is _pool_opening:
            _pool_opening = None
        if live and live.alive:
            _pool_session = live
            return live

    raise TimeoutError("OpenAI realtime connection died during handshake")


def prepare_openai_connection():
    try:
        asyncio.get_running_loop().create_task(ensure_openai_ready())
    except RuntimeError:
        pass


async def take_openai_session():
    global _pool_session
    live = await ensure_openai_ready()
    async with _pool_lock:
        if _pool_session is live:
            _pool_session = None
    prepare_openai_connection()
    if not live.alive:
        raise TimeoutError("OpenAI realtime connection closed before the call")
    return live


async def warmup_openai_realtime():
    for attempt in range(1, 4):
        try:
            await ensure_openai_ready()
            print("✅ OpenAI Realtime warmup ready for the next call")
            return
        except Exception as exc:
            if attempt >= 3:
                print(f"⚠️ OpenAI Realtime warmup failed after retries: {exc!r}")
                return
            print(f"⚠️ OpenAI Realtime warmup retry {attempt}/3: {exc!r}")
            await asyncio.sleep(1.5 * attempt)


def _twilio_ws_subprotocol(ws: WebSocket) -> str | None:
    raw = (ws.headers.get("sec-websocket-protocol") or "").strip()
    if not raw:
        return None
    offered = [part.strip() for part in raw.split(",") if part.strip()]
    for preferred in ("audio.v1", "v1", "twilio"):
        if preferred in offered:
            return preferred
    return offered[0]


@router.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    proto = _twilio_ws_subprotocol(ws)
    print(
        "🔌 WS handshake from",
        ws.client,
        "proto=",
        proto or None,
        "upgrade=",
        ws.headers.get("upgrade"),
    )
    try:
        if proto:
            await ws.accept(subprotocol=proto)
        else:
            await ws.accept()
    except Exception as exc:
        print("❌ WS accept failed:", repr(exc))
        raise
    print("TWILIO MEDIA STREAM CONNECTED")

    stream_info = {
        "sid": None,
        "call_sid": None,
        "member_id": None,
        "client_id": LLC_CLIENT_ID,
        "phone": None,
        "agent_id": None,
        "agent": {},
        "member": {},
        "studio": {},
        "audio_sent": False,
        "greeting_done": False,
        "user_speaking": False,
        "response_active": False,
        "pitch_unlocked": False,
        "offer_pending": False,
        "crm_loaded": False,
        "awaiting_tool_speech": False,
        "end_call_requested": False,
        "hangup_after_goodbye": False,
        "failed_recovery": False,
        "callback_mode": False,
    }

    twilio_q: asyncio.Queue = asyncio.Queue()
    live = None
    openai = None
    listener_task = None
    crm_task = None

    async def drain_twilio():
        try:
            while True:
                message = await ws.receive_text()
                await twilio_q.put(json.loads(message))
        except WebSocketDisconnect:
            await twilio_q.put({"event": "_disconnect"})
        except Exception as exc:
            print("❌ TWILIO SOCKET ERROR:", repr(exc))
            await twilio_q.put({"event": "_disconnect"})

    drain_task = asyncio.create_task(drain_twilio())

    async def attach_openai():
        nonlocal live, openai, listener_task
        if openai is not None:
            return openai
        live = await take_openai_session()
        openai = live.openai
        print("✅ OpenAI Connected")
        listener_task = asyncio.create_task(
            receive_from_openai(live, ws, stream_info)
        )
        try:
            await openai.session.update(
                session=_session_config(
                    greeting_instructions(
                        stream_info.get("agent"),
                        stream_info.get("member"),
                        stream_info.get("studio"),
                    ),
                    allow_interrupt=False,
                    create_response=False,
                    tools_enabled=False,
                    stream_info=stream_info,
                )
            )
        except Exception as exc:
            print("⚠️ session.update with agent voice failed, retrying default:", repr(exc))
            await openai.session.update(
                session=_session_config(
                    greeting_instructions(
                        stream_info.get("agent"),
                        stream_info.get("member"),
                        stream_info.get("studio"),
                    ),
                    allow_interrupt=False,
                    create_response=False,
                    tools_enabled=False,
                )
            )
        print("✅ OpenAI Session Ready")
        return openai

    try:
        while True:
            data = await twilio_q.get()
            event = data.get("event")

            if event == "_disconnect":
                print("📞 WebSocket Disconnected")
                break

            if event != "media":
                print("TWILIO EVENT:", event)

            if event == "start":
                start = data.get("start") or {}
                custom = start.get("customParameters") or {}

                stream_info["sid"] = start.get("streamSid")
                stream_info["call_sid"] = start.get("callSid")
                stream_info["member_id"] = _clean(custom.get("member_id"))
                stream_info["client_id"] = _clean(custom.get("client_id")) or LLC_CLIENT_ID
                stream_info["phone"] = _clean(custom.get("phone"))
                stream_info["agent_id"] = _clean(custom.get("agent_id"))

                print("📞", stream_info["call_sid"], stream_info["phone"])
                mark_media_stream_started(stream_info.get("call_sid"))

                await asyncio.gather(
                    load_agent_context(stream_info),
                    load_crm_context(stream_info),
                )

                try:
                    await attach_openai()
                except Exception as exc:
                    print("❌ OPENAI REALTIME CONNECT FAILED:", repr(exc))
                    break

                print("🤖 Starting greeting")
                await openai.response.create(
                    response={
                        "output_modalities": ["audio"],
                        "instructions": greeting_instructions(
                            stream_info.get("agent"),
                            stream_info.get("member"),
                            stream_info.get("studio"),
                        ),
                    }
                )

            elif event == "media":
                if openai is None or not stream_info.get("greeting_done"):
                    continue
                await openai.input_audio_buffer.append(
                    audio=data["media"]["payload"]
                )

            elif event == "stop":
                print(
                    "📞 Call Ended (Twilio stream stop)",
                    stream_info.get("call_sid"),
                    "greeting_done=",
                    stream_info.get("greeting_done"),
                    "pitch_unlocked=",
                    stream_info.get("pitch_unlocked"),
                    "end_call_requested=",
                    stream_info.get("end_call_requested"),
                )
                break

    except Exception as e:
        print("❌ MEDIA STREAM ERROR:", repr(e))

    finally:
        await _cancel_task(drain_task)
        await _cancel_task(listener_task)
        await _cancel_task(crm_task)
        if live is not None:
            await live.close()
        prepare_openai_connection()
