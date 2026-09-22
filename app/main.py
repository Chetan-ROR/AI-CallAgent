from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.openai.client import client
from app.twilio.voice import router as voice_router
from app.openai.media_stream import router as media_router
from app.api.prompt import router as prompt_router
from app.api.practice import router as practice_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
        "http://localhost:3005",
        "http://127.0.0.1:3005",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "Gym AI POC Running", "status": "ok"}


@app.get("/test-openai")
def test_openai():

    response = client.responses.create(
        model="gpt-5.5",
        input="Say Hello from OpenAI."
    )

    return {
        "response": response.output_text
    }


app.include_router(voice_router)
app.include_router(media_router)
app.include_router(prompt_router)
app.include_router(practice_router)
