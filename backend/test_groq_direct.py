import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key or not api_key.strip():
    raise SystemExit("ERROR: GROQ_API_KEY is missing or empty.")

client = OpenAI(
    api_key=api_key.strip(),
    base_url="https://api.groq.com/openai/v1",
    timeout=30.0,
)

try:
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly: Groq connection successful",
            }
        ],
        max_tokens=30,
    )

    print("SUCCESS")
    print(response.choices[0].message.content)

except Exception as exc:
    print("FAILED")
    print("Error type:", type(exc).__name__)
    print("HTTP status:", getattr(exc, "status_code", "Not available"))
    print("Check the Groq model, endpoint, and API credentials.")