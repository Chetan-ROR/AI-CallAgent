from fastapi import FastAPI
from app.openai.client import client
from app.twilio.voice import router as voice_router
from app.openai.media_stream import router as media_router

app = FastAPI()


@app.get("/")
def home():
    return {"message": "Gym AI POC Running"}


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