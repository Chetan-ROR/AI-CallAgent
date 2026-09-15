from twilio.rest import Client
import os


async def end_call(call_sid: str):

    try:

        client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN"),
        )

        call = client.calls(call_sid).update(
            status="completed"
        )

        print(f"📞 Call ended successfully: {call.sid}")

        return {
            "success": True,
            "call_sid": call.sid
        }

    except Exception as e:

        print(f"❌ Failed to end call: {e}")

        return {
            "success": False,
            "error": str(e)
        }