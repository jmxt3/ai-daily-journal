import os
import logging
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.cloud import secretmanager

# Load non-sensitive config from .env (GOOGLE_CLOUD_PROJECT, MODEL, etc.)
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "github-ai-daily-journal")


def get_secret(secret_id: str) -> str:
    """Return secret value from env var (Cloud Run injection) or
    fall back to Secret Manager using Application Default Credentials
    (local dev: run `gcloud auth application-default login` once)."""
    value = os.environ.get(secret_id)
    if value:
        return value
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        logger.info(f"Loaded '{secret_id}' from Secret Manager")
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logger.error(f"Failed to load '{secret_id}' from Secret Manager: {e}")
        return None


# Secrets — injected by Cloud Run in prod, fetched from Secret Manager locally
GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
API_KEY = get_secret("API_KEY")
MODEL = os.environ.get("MODEL", "gemini-2.0-flash-lite")

app = FastAPI()
security = HTTPBearer()

def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not API_KEY or credentials.credentials != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials

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
async def generate_note(api_key: str = Depends(verify_api_key)):
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not configured")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service unavailable")

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
            note_content += chunk.text or ""

        return {
            "note": note_content,
            "date": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error generating note: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"[DEBUG] {str(e)}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
