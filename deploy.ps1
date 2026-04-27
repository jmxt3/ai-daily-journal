Write-Host "🚀 Deploying AI Daily Journal to Cloud Run..." -ForegroundColor Cyan
gcloud builds submit --config cloudbuild.yaml .
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Deployment submitted successfully!" -ForegroundColor Green
} else {
    Write-Host "❌ Deployment failed." -ForegroundColor Red
    exit 1
}
