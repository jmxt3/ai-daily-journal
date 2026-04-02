import requests
import os
import sys
import datetime

# Get secrets from environment
api_key = os.getenv("AI_API_KEY")  # Your GEMINI_API_KEY or custom key
endpoint = os.getenv(
    "AI_API_ENDPOINT"
)  # e.g. https://your-cloud-run-url.a.run.app/generate-note

if not endpoint:
    print("Error: AI_API_ENDPOINT secret is not set")
    sys.exit(1)

# Optional: pass the key if your endpoint still checks it
headers = {"Content-Type": "application/json"}
if api_key:
    headers["Authorization"] = (
        f"Bearer {api_key}"  # or "X-API-Key" depending on your FastAPI
    )



try:
    response = requests.post(endpoint, headers=headers, timeout=60)
    response.raise_for_status()

    data = response.json()

    # Extract the note
    note = (
        data.get("note") or data.get("content") or data.get("text") or str(data)
    ).strip()

    if not note:
        note = "No note content returned from API."

    print(note)

except requests.exceptions.RequestException as e:
    print(f"API request failed: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Unexpected error: {e}", file=sys.stderr)
    sys.exit(1)
