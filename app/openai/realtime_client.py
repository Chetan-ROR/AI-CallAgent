import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


async def connect():

    print("Connecting to OpenAI...\n")

    async with client.realtime.connect(model="gpt-realtime-2") as connection:

        print("✅ Connected!")

        await connection.session.update(
            session={
                "type": "realtime",
                "instructions": "You are a professional Sales Agent for Total Gym.",
                "output_modalities": ["text"]
            }
        )

        print("Session Ready\n")

        while True:

            user_input = input("\nYou : ")

            if user_input.lower() in ["exit", "quit"]:
                print("Closing connection...")
                break

            await connection.conversation.item.create(
                item={
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_input
                        }
                    ]
                }
            )

            await connection.response.create()

            print("\nAI : ", end="", flush=True)

            async for event in connection:

                if event.type == "response.output_text.delta":
                    print(event.delta, end="", flush=True)

                elif event.type == "response.done":
                    print()
                    break


if __name__ == "__main__":
    asyncio.run(connect())