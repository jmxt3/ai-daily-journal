import os
import logging
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Constants
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MODEL = os.environ.get("MODEL")

app = FastAPI()

ASCII_ART = r"""
    ___  ____   ___       _ __        __                       __ 
   / _ |/  _/  / _ \___ _(_) /_ __   / /__  __ _________  ___ / / 
  / __ |/ /   / // / _ `/ / / // /  / / _ \/ // / __/ _ \/ _ `/ / 
 /_/ |_/___/ /____/\_,_/_/_/\_, /  /_/\___/\_,_/_/ /_//_/\_,_/_/  
                           /___/                                  

🚀 Welcome to the AI Daily Journal API!
=======================================
Everything is running perfectly.

"""


@app.get("/", response_class=PlainTextResponse)
async def root():
    return ASCII_ART


@app.get("/health", tags=["System"])
def health_check():
    return {"status": "healthy"}


@app.post("/generate-note")
async def generate_note():
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY environment variable not set"}

    try:
        client = genai.Client(
            api_key=GEMINI_API_KEY,
        )

        model = MODEL

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        prompt_text = f"""Generate a short, unique, positive daily note about Agenting AI, fine tuning multi-modal, generative ai, RAG, MModel Distillation, Open-weight models, Transformes, vLLM, or personal growth.
Today is {today}. Keep it under 60 words. Be technical, PhD level, Make it insightful and inspiring."""

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt_text),
                ],
            ),
        ]
        generate_content_config = types.GenerateContentConfig()

        note_content = ""
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=generate_content_config,
        ):
            note_content += chunk.text

        return {
            "note": note_content,
            "date": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error generating note: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
