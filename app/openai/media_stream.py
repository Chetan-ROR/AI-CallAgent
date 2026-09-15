from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.prompts import F45_SYSTEM_PROMPT
from app.tools.end_call import end_call

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

    function_calls = {}

    try:

        async for event in openai:

            print("=" * 60)
            print("EVENT:", event.type)

            # ----------------------------------------
            # OpenAI → Twilio Audio
            # ----------------------------------------
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

                else:

                    print("⚠️ Twilio Stream SID not available yet")

            # ----------------------------------------
            # OpenAI Audio Transcript
            # ----------------------------------------
            elif event.type == "response.output_audio_transcript.delta":

                print("📝", event.delta)

            # ----------------------------------------
            # Function Call Arguments
            # ----------------------------------------
            elif event.type == "response.function_call_arguments.delta":

                print("🔧 Function arguments delta:", event.delta)

                call_id = event.call_id

                if call_id not in function_calls:
                    function_calls[call_id] = ""

                function_calls[call_id] += event.delta

            # ----------------------------------------
            # Function Call Completed
            # ----------------------------------------
            elif event.type == "response.function_call_arguments.done":

                print("🔧 Function call completed")

                call_id = event.call_id
                function_name = event.name

                arguments = function_calls.get(call_id, "")

                print("🔧 Function name:", function_name)
                print("🔧 Arguments:", arguments)

                # ----------------------------------------
                # END CALL
                # ----------------------------------------
                if function_name == "end_call":

                    print("📞 END CALL TOOL TRIGGERED")

                    call_sid = stream_info.get("call_sid")

                    if not call_sid:

                        print("❌ Call SID not available")

                        function_calls.pop(call_id, None)

                        continue

                    result = await end_call(call_sid)

                    print("📞 END CALL RESULT:", result)

                    function_calls.pop(call_id, None)

            # ----------------------------------------
            # Response Done
            # ----------------------------------------
            elif event.type == "response.done":

                print("✅ RESPONSE DONE")

            # ----------------------------------------
            # OpenAI Error
            # ----------------------------------------
            elif event.type == "error":

                print("❌ OPENAI ERROR")
                print(event)

            # ----------------------------------------
            # Other Events
            # ----------------------------------------
            else:

                print("ℹ️", event.type)

            print("=" * 60)

    except Exception as e:

        print("=" * 60)
        print("❌ OPENAI LISTENER ERROR")
        print("ERROR:", repr(e))
        print("=" * 60)


@router.websocket("/media-stream")
async def media_stream(ws: WebSocket):

    await ws.accept()

    print("=" * 60)
    print("TWILIO MEDIA STREAM CONNECTED")
    print("=" * 60)

    stream_info = {
        "sid": None,
        "call_sid": None
    }

    async with client.realtime.connect(
        model="gpt-realtime-2"
    ) as openai:

        print("✅ OpenAI Connected")

        # ----------------------------------------
        # OpenAI Session Configuration
        # ----------------------------------------
        await openai.session.update(
            session={
                "type": "realtime",

                "instructions": F45_SYSTEM_PROMPT,

                # ----------------------------------------
                # Tools
                # ----------------------------------------
                "tools": [
                    {
                        "type": "function",
                        "name": "end_call",
                        "description": (
                            "End the current phone call when the customer clearly "
                            "says they are busy, asks to be called another time, "
                            "or clearly wants to end the conversation."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                ],

                "tool_choice": "auto",

                # ----------------------------------------
                # Audio
                # ----------------------------------------
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

        # ----------------------------------------
        # Start OpenAI Event Listener FIRST
        # ----------------------------------------
        asyncio.create_task(
            receive_from_openai(
                openai,
                ws,
                stream_info
            )
        )

        print("👂 OpenAI listener started")

        # ----------------------------------------
        # Twilio WebSocket Loop
        # ----------------------------------------
        try:

            while True:

                message = await ws.receive_text()

                data = json.loads(message)

                event = data["event"]

                # ----------------------------------------
                # Twilio Stream Started
                # ----------------------------------------
                if event == "start":

                    stream_info["sid"] = data["start"]["streamSid"]

                    stream_info["call_sid"] = data["start"]["callSid"]

                    print(
                        "🎯 Stream SID:",
                        stream_info["sid"]
                    )

                    print(
                        "📞 Call SID:",
                        stream_info["call_sid"]
                    )

                    # ----------------------------------------
                    # IMPORTANT:
                    # Start the AI conversation AFTER
                    # Twilio gives us the Stream SID.
                    # ----------------------------------------
                    print("🤖 Asking AI to start conversation")

                    await openai.response.create(
                        response={
                            "output_modalities": ["audio"]
                        }
                    )

                    print("🤖 Initial AI response requested")

                # ----------------------------------------
                # Twilio Audio → OpenAI
                # ----------------------------------------
                elif event == "media":

                    await openai.input_audio_buffer.append(
                        audio=data["media"]["payload"]
                    )

                # ----------------------------------------
                # Twilio Stream Stopped
                # ----------------------------------------
                elif event == "stop":

                    print("📞 Call Ended")

                    break

        except WebSocketDisconnect:

            print("📞 WebSocket Disconnected")

        except Exception as e:

            print("=" * 60)
            print("❌ MEDIA STREAM ERROR")
            print("ERROR:", repr(e))
            print("=" * 60)