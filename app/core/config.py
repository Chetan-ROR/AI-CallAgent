from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

LLC_API_URL = os.getenv("LLC_API_URL", "http://127.0.0.1:3000/api/v1")
LLC_AI_API_KEY = os.getenv("LLC_AI_API_KEY")
LLC_CLIENT_ID = os.getenv("LLC_CLIENT_ID")
# When make-call does not pass agent_id, LLC resolves this key/name (e.g. "Receptionist").
LLC_CALL_AGENT_KEY = (os.getenv("LLC_CALL_AGENT_KEY") or "receptionist").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
# Optional explicit HTTPS base for Twilio Media Stream WSS only (empty = auto cloudflared on dev tunnel).
STREAM_PUBLIC_BASE_URL = (os.getenv("STREAM_PUBLIC_BASE_URL") or "").strip().rstrip("/")
AUTO_CLOUDFLARED_FOR_WSS = os.getenv("AUTO_CLOUDFLARED_FOR_WSS", "1").lower() in (
    "1",
    "true",
    "yes",
)
# If true, block make-call when local WSS probe to /media-stream fails (often false-negative on dev tunnels).
STRICT_WSS_PROBE = os.getenv("STRICT_WSS_PROBE", "").lower() in ("1", "true", "yes")
TEST_CALL_PHONE = os.getenv("TEST_CALL_PHONE", "+918458916116")
APP_PORT = int(os.getenv("APP_PORT", "8004"))

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY not found in .env")
