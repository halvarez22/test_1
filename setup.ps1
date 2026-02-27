# Script de Configuración Inicial para LicitAI
Write-Host "Iniciando configuración de LicitAI..." -ForegroundColor Cyan

# 1. Crear directorios de datos si no existen
$dirs = @(
    "data/db",
    "data/workspaces",
    "data/embeddings",
    "data/models"
)

foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force
        Write-Host "Creado: $dir" -ForegroundColor Green
    }
}

# 2. Copiar .env si no existe
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Creado archivo .env desde .example. Por favor edítalo con tus claves." -ForegroundColor Yellow
}

# 3. Instrucciones finales
Write-Host "`nConfiguración completada con éxito." -ForegroundColor Cyan
Write-Host "Para iniciar la aplicación, ejecuta:" -ForegroundColor White
Write-Host "docker compose up -d --build" -ForegroundColor Green
Write-Host "`nRecuerda tener Ollama instalado y los modelos descargados:" -ForegroundColor White
Write-Host "ollama pull llama3.1:8b" -ForegroundColor Green
Write-Host "ollama pull nomic-embed-text:latest" -ForegroundColor Green
