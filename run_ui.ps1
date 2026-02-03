$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repo

$py = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
  Write-Host "Missing .venv. Creating it..."
  python -m venv .venv
}

& $py -m pip install -r requirements.txt
& $py -m evidence_pack_engine.desktop
