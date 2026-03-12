# RecallHub Docker Image Loader
# Run this to load all container images

Write-Host "Loading RecallHub container images..." -ForegroundColor Cyan

foreach ($img in Get-ChildItem "*.tar" -Exclude "ollama-models.tar") {
    Write-Host "Loading $($img.Name)..."
    docker load -i $img.FullName
}

Write-Host "Starting services..."
docker compose up -d

Write-Host "Done! Access RecallHub at http://localhost:11080"
