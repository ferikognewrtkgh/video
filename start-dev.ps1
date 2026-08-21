$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:MANGAFLOW_RELOAD = "true"
Start-Process -FilePath "python" -ArgumentList "run.py" -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory (Join-Path $root "frontend") -WindowStyle Hidden
Write-Host "MangaFlow Studio is starting..."
Write-Host "Studio: http://localhost:3000"
Write-Host "API docs: http://localhost:8000/docs"
