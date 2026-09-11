from fastapi import APIRouter
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client
import os
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)


@router.post("/incoming-call")
async def incoming_call():

    response = VoiceResponse()

    connect = response.connect()

    connect.stream(
        url="wss://d4hxdf6n-8000.inc1.devtunnels.ms/media-stream"
    )

    return Response(
        content=str(response),
        media_type="application/xml"
    )

@router.post("/make-call")
async def make_call():

    call = client.calls.create(
        to="+919996534774",   # <-- apna verified mobile number
        from_=os.getenv("TWILIO_PHONE_NUMBER"),
        url="https://d4hxdf6n-8000.inc1.devtunnels.ms/incoming-call"
    )
    print(call.sid)

    return {
        "status": "Calling...",
        "call_sid": call.sid
    }