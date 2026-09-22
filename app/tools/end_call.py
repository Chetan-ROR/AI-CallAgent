from app.twilio.twilio_client import twilio_client


async def end_call(call_sid: str):

    try:

        call = twilio_client.calls(call_sid).update(
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