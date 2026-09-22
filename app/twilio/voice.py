from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
import os
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

router = APIRouter()

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://d4hxdf6n-8000.inc1.devtunnels.ms",
)
DEFAULT_TO_NUMBER = os.getenv("DEFAULT_TO_NUMBER", "+918458916116")

client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)


class MakeCallRequest(BaseModel):
    to: str = Field(
        default=DEFAULT_TO_NUMBER,
        description="E.164 phone number to call, e.g. +919876543210",
    )


@router.post("/incoming-call")
async def incoming_call():

    response = VoiceResponse()

    connect = response.connect()

    connect.stream(
        url="wss://{}/media-stream".format(
            PUBLIC_BASE_URL.replace("https://", "").replace("http://", "")
        )
    )

    return Response(
        content=str(response),
        media_type="application/xml"
    )


@router.post("/make-call")
async def make_call(body: Optional[MakeCallRequest] = None):
    to_number = (body.to if body else None) or DEFAULT_TO_NUMBER
    from_number = os.getenv("TWILIO_PHONE_NUMBER")

    if not from_number:
        raise HTTPException(status_code=500, detail="TWILIO_PHONE_NUMBER not set")

    if not to_number.startswith("+"):
        raise HTTPException(
            status_code=400,
            detail="Phone number must be in E.164 format, e.g. +919876543210",
        )

    call = client.calls.create(
        to=to_number,
        from_=from_number,
        url="{}/incoming-call".format(PUBLIC_BASE_URL.rstrip("/")),
    )
    print(call.sid)

    return {
        "status": "Calling...",
        "call_sid": call.sid,
        "to": to_number,
    }
