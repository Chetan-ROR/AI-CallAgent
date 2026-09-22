# AI-CallAgent

Gym voice AI receptionist — Twilio calls bridged to OpenAI Realtime.

## Backend

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Env vars: `OPENAI_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`.  
Optional: `PUBLIC_BASE_URL`, `DEFAULT_TO_NUMBER`.

## Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Set `NEXT_PUBLIC_API_URL` in `frontend/.env.local` if the API is not on `:8000`.

## Practice chat (no phone call)

Train the same voice agent in the browser:

1. Open [http://localhost:3000/practice](http://localhost:3000/practice)
2. Click **Start practice call** and allow the microphone
3. The agent speaks first, same prompt as a live Twilio call
4. Talk back or type in the chat box

Uses OpenAI Realtime over WebRTC. Twilio is not involved.
