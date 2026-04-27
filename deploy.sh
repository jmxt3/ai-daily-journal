#!/bin/bash
set -e

echo "🚀 Deploying AI Daily Journal to Cloud Run..."
gcloud builds submit --config cloudbuild.yaml .
echo "✅ Deployment submitted!"
