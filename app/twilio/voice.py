from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse
from twilio.base.exceptions import TwilioRestException
import asyncio
import os
import time
from threading import Lock
from dotenv import load_dotenv

from app.core.config import (
    LLC_CALL_AGENT_KEY,
    LLC_CLIENT_ID,
    PUBLIC_BASE_URL,
    TEST_CALL_PHONE,
    TWILIO_PHONE_NUMBER,
)
from app.core.public_webhook import (
    mark_public_webhook_ok,
    public_http_and_ws_bases,
    public_urls_for_request,
    public_webhook_help,
    resolve_media_stream_wss_url,
    resolve_twilio_stream_base,
)
from app.twilio.twilio_client import twilio_client as client
from app.llc.client import LlcClient, save_agent_prefetch, save_crm_prefetch
from app.openai.media_stream import ensure_openai_ready, prepare_openai_connection
from app.twilio.call_tracking import note_call_status

load_dotenv()

router = APIRouter()


async def _twilio_form_payload(request: Request) -> dict:
    try:
        return dict(await asyncio.wait_for(request.form(), timeout=2.0))
    except Exception:
        try:
            return dict(request.query_params)
        except Exception:
            return {}


# Prevent double-click / duplicate POST /make-call from starting two Twilio legs to one phone.
_DIAL_GUARD = Lock()
_LAST_DIAL: dict[str, tuple[float, str]] = {}
DIAL_COOLDOWN_SEC = float(os.getenv("MAKE_CALL_COOLDOWN_SEC", "25"))
_make_call_lock = asyncio.Lock()


def _cancel_stale_outbound_to(phone: str) -> str | None:
    """Hang up queued/ringing duplicates; block if someone is already in-progress."""
    try:
        active = client.calls.list(to=phone, limit=8)
    except Exception as exc:
        print("⚠️ Could not list Twilio calls for dedupe:", repr(exc))
        return None

    in_progress = [c for c in active if c.status == "in-progress"]
    if in_progress:
        return in_progress[0].sid

    for call in active:
        if call.status in {"queued", "ringing"}:
            try:
                client.calls(call.sid).update(status="completed")
                print(f"📞 Cancelled stale outbound call {call.sid} → {phone}")
            except Exception as exc:
                print(f"⚠️ Could not cancel {call.sid}:", repr(exc))
    return None


def _dial_cooldown_block(phone: str) -> tuple[str, float] | None:
    with _DIAL_GUARD:
        last = _LAST_DIAL.get(phone)
        if not last:
            return None
        age = time.monotonic() - last[0]
        if age < DIAL_COOLDOWN_SEC:
            return last[1], age
    return None


def _record_dial(phone: str, call_sid: str) -> None:
    with _DIAL_GUARD:
        _LAST_DIAL[phone] = (time.monotonic(), call_sid)


class MakeCallRequest(BaseModel):
    member_id: str | None = None
    client_id: str | None = None
    agent_id: str | None = None
    phone: str | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "phone": "+919996534774",
                    "member_id": "69aed34b9e97ef75839893b2",
                    "client_id": "studio-client-id",
                }
            ]
        }
    }


PLACEHOLDER_VALUES = {"", "string", "null", "none", "undefined"}


def clean_id(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.lower() in PLACEHOLDER_VALUES:
        return None
    return stripped



def to_e164(phone: str | None) -> str | None:
    if not phone:
        return None

    raw = phone.strip()
    if not raw:
        return None

    if raw.startswith("00"):
        digits = "".join(ch for ch in raw[2:] if ch.isdigit())
        return f"+{digits}" if len(digits) >= 8 else None

    if not raw.startswith("+"):
        return None

    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 8:
        return None
    return f"+{digits}"


def _build_stream_twiml(
    *,
    stream_url: str,
    member_id=None,
    client_id=None,
    agent_id=None,
    phone=None,
    status_callback=None,
) -> str:
    response = VoiceResponse()
    response.say("One moment please.")
    connect = response.connect()
    stream_kwargs = {"url": stream_url}
    if status_callback:
        stream_kwargs["status_callback"] = status_callback
        stream_kwargs["status_callback_event"] = "started completed error"
    stream = connect.stream(**stream_kwargs)
    if member_id:
        stream.parameter(name="member_id", value=member_id)
    if client_id:
        stream.parameter(name="client_id", value=client_id)
    if agent_id:
        stream.parameter(name="agent_id", value=agent_id)
    if phone:
        stream.parameter(name="phone", value=phone)
    return str(response)


def _twiml_stream_response(*, stream_url: str, member_id=None, client_id=None, agent_id=None, phone=None, status_callback=None) -> Response:
    return Response(
        content=_build_stream_twiml(
            stream_url=stream_url,
            member_id=member_id,
            client_id=client_id,
            agent_id=agent_id,
            phone=phone,
            status_callback=status_callback,
        ),
        media_type="text/xml",
    )


@router.api_route("/incoming-call", methods=["GET", "POST"], include_in_schema=False)
async def incoming_call(
    request: Request,
    member_id: str | None = None,
    client_id: str | None = None,
    agent_id: str | None = None,
    phone: str | None = None,
):
    call_sid = None
    try:
        if request.method == "POST":
            form = dict(await asyncio.wait_for(request.form(), timeout=2.0))
            call_sid = form.get("CallSid")
    except Exception:
        pass
    print(
        "📥 Twilio incoming-call",
        f"CallSid={call_sid}",
        f"member_id={member_id}",
        f"client_id={client_id}",
        f"phone={phone}",
    )
    try:
        from_request = public_urls_for_request(
            request.headers.get("x-forwarded-proto"),
            request.headers.get("x-forwarded-host") or request.headers.get("host"),
        )
        http_base, _ = from_request or public_http_and_ws_bases(PUBLIC_BASE_URL)
        stream_url, wss_base, wss_err = await resolve_media_stream_wss_url(http_base)
        if wss_err or not stream_url:
            print("❌ incoming-call: no Media Stream WSS:", wss_err)
            vr = VoiceResponse()
            vr.say("Sorry, our voice system is temporarily unavailable. Please try again later.")
            return Response(content=str(vr), media_type="text/xml")
        if wss_base and wss_base.rstrip("/") != http_base.rstrip("/"):
            print("🎧 Media Stream WSS host:", wss_base, "(HTTP callbacks:", http_base + ")")
        resolved_client_id = client_id or LLC_CLIENT_ID
        resolved_phone = phone

        prepare_openai_connection()
        mark_public_webhook_ok(http_base)
        print("🎧 TwiML stream:", stream_url)
        return _twiml_stream_response(
            stream_url=stream_url,
            member_id=member_id,
            client_id=resolved_client_id,
            agent_id=agent_id,
            phone=resolved_phone,
            status_callback=f"{http_base}/stream-status",
        )
    except Exception as exc:
        print("❌ incoming-call failed, returning fallback TwiML:", repr(exc))
        http_base, _ = public_http_and_ws_bases(PUBLIC_BASE_URL)
        stream_url, _, wss_err = await resolve_media_stream_wss_url(http_base)
        if wss_err or not stream_url:
            vr = VoiceResponse()
            vr.say("Sorry, our voice system is temporarily unavailable.")
            return Response(content=str(vr), media_type="text/xml")
        return _twiml_stream_response(
            stream_url=stream_url,
            status_callback=f"{http_base}/stream-status",
        )


@router.api_route("/stream-status", methods=["GET", "POST"], include_in_schema=False)
async def stream_status(request: Request):
    payload = await _twilio_form_payload(request)
    event = payload.get("StreamEvent") or payload.get("streamEvent")
    if event in {"stream-error", "error"}:
        print("❌ Twilio MEDIA STREAM ERROR:", payload)
    elif event and event != "stream-started":
        print("⚠️ Twilio media stream event:", event, payload)
    else:
        print("📡 Twilio stream status:", payload)
    return Response(content="ok", media_type="text/plain")


@router.api_route("/call-status", methods=["GET", "POST"], include_in_schema=False)
async def call_status(request: Request):
    """Twilio outbound call lifecycle — explains failed / busy legs (not AI hangup)."""
    payload = await _twilio_form_payload(request)
    status = payload.get("CallStatus")
    sid = payload.get("CallSid")
    err = payload.get("ErrorMessage") or payload.get("ErrorCode")
    if err or status in {"failed", "busy", "no-answer", "canceled"}:
        print(
            "⚠️ Twilio call status:",
            sid,
            status,
            err or "",
            payload.get("SipResponseCode") or "",
        )
    else:
        print("📡 Twilio call status:", sid, status)
    if status == "completed":
        duration = payload.get("CallDuration")
        print(f"📞 Call duration: {duration}s", sid)
        try:
            if duration is not None and int(duration) < 8:
                print(
                    "⚠️ Call ended within a few seconds of answer —",
                    "check uvicorn for 'Media stream connected'.",
                    "If missing, Twilio could not open wss://YOUR-TUNNEL/media-stream (common on dev tunnels).",
                )
        except (TypeError, ValueError):
            pass
    note_call_status(sid, status)
    return Response(content="ok", media_type="text/plain")


@router.post("/make-call")
async def make_call(payload: MakeCallRequest | None = Body(default=None)):
    payload = payload or MakeCallRequest()
    client_id = clean_id(payload.client_id) or LLC_CLIENT_ID
    member_id = clean_id(payload.member_id)
    agent_id = clean_id(payload.agent_id)
    phone = to_e164(payload.phone or TEST_CALL_PHONE)

    if not phone:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": (
                    "Provide phone in E.164 format with country code, e.g. +919996534774 or +14155551234"
                ),
            },
        )

    async with _make_call_lock:
        return await _place_outbound_call(
            phone=phone,
            client_id=client_id,
            member_id=member_id,
            agent_id=agent_id,
        )


async def _place_outbound_call(
    *,
    phone: str,
    client_id: str | None,
    member_id: str | None,
    agent_id: str | None,
):
    cooldown = _dial_cooldown_block(phone)
    if cooldown:
        prior_sid, age = cooldown
        print(f"⏳ make-call blocked — dialed {phone} {age:.0f}s ago ({prior_sid})")
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "error": (
                    f"A call to this number started {age:.0f}s ago. "
                    "Wait for it to finish before placing another."
                ),
                "call_sid": prior_sid,
            },
        )

    busy_sid = _cancel_stale_outbound_to(phone)
    if busy_sid:
        print(f"⏳ make-call blocked — call in progress {busy_sid} → {phone}")
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "error": "A call to this number is already in progress.",
                "call_sid": busy_sid,
            },
        )

    openai_err = None
    for attempt in range(1, 4):
        try:
            await ensure_openai_ready()
            openai_err = None
            break
        except Exception as exc:
            openai_err = exc
            print(f"⚠️ OpenAI Realtime not ready (attempt {attempt}/3):", repr(exc))
            if attempt < 3:
                await asyncio.sleep(2.0 * attempt)
    if openai_err is not None:
        print("❌ make-call 503: OpenAI Realtime failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "error": f"OpenAI Realtime is not ready: {openai_err}",
            },
        )

    http_base, probe = await resolve_twilio_stream_base(PUBLIC_BASE_URL)
    if not http_base:
        message = public_webhook_help(PUBLIC_BASE_URL, probe)
        print("❌ make-call 503: public URL / tunnel:", probe)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "error": message,
                "public_base_url": PUBLIC_BASE_URL or None,
                "probe": probe,
            },
        )

    stream_url, wss_base, wss_err = await resolve_media_stream_wss_url(http_base)
    if wss_err or not stream_url:
        print("❌ make-call 503: Media Stream WSS:", wss_err)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "error": wss_err or "Media Stream WSS URL not configured",
                "hint": "Wait for server log: ✅ Gym AI ready — then retry.",
            },
        )

    llc = LlcClient()
    if llc.enabled:
        agent_lookup = await llc.lookup_agent(
            client_id=client_id,
            agent_id=agent_id,
            agent_key=None if agent_id else LLC_CALL_AGENT_KEY,
        )
        agent = agent_lookup.get("agent") if agent_lookup.get("success") else None
        if agent and agent.get("id"):
            agent_id = agent.get("id")
            studio_id = agent.get("client_id") or client_id
            if studio_id:
                client_id = studio_id
            save_agent_prefetch(client_id, agent_id, agent_lookup)
            print("🤖 Agent loaded before dial:", agent.get("name"), agent_id)
        else:
            print(
                "⚠️ No LLC agent loaded:",
                (agent_lookup or {}).get("error") or "no active agent for this client",
            )

        lookup = await llc.lookup_member(client_id=client_id, member_id=member_id)
        studio = lookup.get("studio") or {}
        if studio.get("id"):
            client_id = studio.get("id")
        if lookup.get("success") and studio:
            save_crm_prefetch(client_id, member_id, lookup)
            class_catalog = studio.get("classes") or []
            schedule_rows = sum(
                len(item.get("schedules") or [])
                for item in class_catalog
                if isinstance(item, dict)
            )
            print(
                "👤 Studio catalog loaded:",
                len(studio.get("class_names") or []),
                "classes,",
                len(class_catalog),
                "with schedules,",
                schedule_rows,
                "schedule rows,",
                len(studio.get("membership_plans") or []),
                "plans",
            )
            if lookup.get("found") and lookup.get("member"):
                print("👤 CRM member:", lookup.get("member", {}).get("first_name"), member_id)
        else:
            print(
                "⚠️ Studio catalog not loaded:",
                lookup.get("error") or "LLC lookup failed",
            )
    elif member_id and client_id:
        print("📞 LLC API is not configured — placing call without CRM")
    else:
        print("📞 No LLC catalog — placing call without classes/plans")

    print("🌤️ Public base for Twilio:", http_base)
    if wss_base and wss_base.rstrip("/") != http_base.rstrip("/"):
        print("🎧 Media Stream WSS host:", wss_base, "(HTTP callbacks:", http_base + ")")
    twiml = _build_stream_twiml(
        stream_url=stream_url,
        member_id=member_id,
        client_id=client_id,
        agent_id=agent_id,
        phone=phone,
        status_callback=f"{http_base}/stream-status",
    )
    print("🎧 Outbound call uses inline TwiML (no answer-time webhook):", stream_url)
    if member_id:
        print("👤 make-call member_id:", member_id)
    else:
        print(
            "⚠️ make-call has no member_id — use LLC UI Call button or pass member_id in JSON;",
            "Swagger-only tests will not load रमेश CRM profile.",
        )

    try:
        call = client.calls.create(
            to=phone,
            from_=TWILIO_PHONE_NUMBER,
            twiml=twiml,
            status_callback=f"{http_base}/call-status",
            status_callback_event=[
                "initiated",
                "ringing",
                "answered",
                "completed",
                "busy",
                "failed",
                "no-answer",
                "canceled",
            ],
        )
    except TwilioRestException as exc:
        message = exc.msg or str(exc)
        if exc.code == 21219:
            message = (
                f"Twilio trial accounts can only call verified numbers. "
                f"{phone} is not verified. Verify it in Twilio Console, "
                "or call a verified number such as +918458916116."
            )
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": message,
                "twilio_code": exc.code,
                "to": phone,
            },
        )

    _record_dial(phone, call.sid)
    print(call.sid)

    return {
        "status": "Calling...",
        "call_sid": call.sid,
        "to": phone,
        "member_id": member_id,
        "client_id": client_id,
        "agent_id": agent_id,
        "stream_url": stream_url,
        "twiml_mode": "inline",
    }
