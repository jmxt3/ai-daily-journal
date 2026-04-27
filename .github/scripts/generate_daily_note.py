import requests
import os
import sys
import datetime

# Get secrets from environment
api_key = os.getenv("AI_API_KEY")       # Custom Bearer API_KEY for app-level auth
gcp_token = os.getenv("CLOUDSDK_AUTH_ACCESS_TOKEN")  # GCP identity token for Cloud Run IAM
endpoint = os.getenv(
    "AI_API_ENDPOINT"
)  # e.g. https://your-cloud-run-url.a.run.app/generate-note

if not endpoint:
    print("Error: AI_API_ENDPOINT secret is not set")
    sys.exit(1)

# Build headers — GCP identity token satisfies Cloud Run IAM,
# API_KEY satisfies the FastAPI Bearer check.
headers = {"Content-Type": "application/json"}
if gcp_token:
    headers["Authorization"] = f"Bearer {gcp_token}"
elif api_key:
    # Fallback for local/manual testing without GCP auth
    headers["Authorization"] = f"Bearer {api_key}"


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
