from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import asyncio
import os
import json

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

router = APIRouter()


async def receive_from_openai(openai, ws, stream_info):

    async for event in openai:

        print("=" * 60)

        if event.type == "response.output_audio.delta":

            print("🔊 Sending audio to Twilio")
            print(type(event.delta))
            print(len(event.delta))

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
                print("🔊 AUDIO SENT TO TWILIO")

        elif event.type == "response.output_audio_transcript.delta":

            print("📝", event.delta)

        elif event.type == "response.done":

            print("✅ RESPONSE DONE")

        elif event.type == "error":

            print("❌ ERROR")
            print(event)

        else:

            print(event.type)

        print("=" * 60)


@router.websocket("/media-stream")
async def media_stream(ws: WebSocket):

    await ws.accept()

    print("=" * 60)
    print("TWILIO MEDIA STREAM CONNECTED")
    print("=" * 60)

    stream_info = {
        "sid": None
    }

    async with client.realtime.connect(
        model="gpt-realtime-2"
    ) as openai:

        print("✅ OpenAI Connected")

        await openai.session.update(
            session={
                "type": "realtime",
                "instructions": "You are a helpful gym receptionist.",
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcmu"
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "create_response": True,
                            "interrupt_response": True
                        }
                    },
                    "output": {
                        "format": {
                            "type": "audio/pcmu"
                        }
                    }
                }
            }
        )

        print("✅ OpenAI Session Ready")

        asyncio.create_task(
            receive_from_openai(openai, ws, stream_info)
        )

        try:

            while True:

                message = await ws.receive_text()

                data = json.loads(message)

                event = data["event"]

                if event == "start":

                    stream_info["sid"] = data["start"]["streamSid"]

                    print("🎯 Stream SID:", stream_info["sid"])

                elif event == "media":

                    await openai.input_audio_buffer.append(
                        audio=data["media"]["payload"]
                    )

                elif event == "stop":

                    print("📞 Call Ended")
                    break

        except WebSocketDisconnect:

            print("📞 WebSocket Disconnected")