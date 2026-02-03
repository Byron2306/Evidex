$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $repo
Set-Location $repo

Write-Host "EVIDEX local setup (repo): $repo"

$envFile = Join-Path $repo 'evidex.env'
if (-not (Test-Path $envFile)) {
  @(
    '# EVIDEX runtime settings',
    '# This file is read by the CLI and desktop UI.',
    'EVIDEX_WATCH_ROOT=',
    'PAYMENT_LINK=',
    'PAYPAL_LINK=https://paypal.me/EVIDEX',
    'REQUIRE_PAYMENT=0',
    '',
    '# Optional operator shortcuts (Google intake assets)',
    '# You can paste either the raw ID or the full URL.',
    'EVIDEX_GOOGLE_FORM_ID=https://docs.google.com/forms/d/e/1FAIpQLSf5aux5aHI0dSbZQHiPQCxX6i--w0BZ-iVC7gONNgla7lqNPw/viewform',
    'EVIDEX_GOOGLE_DRIVE_FOLDER_ID=',
    '',
    '# Optional: run Apps Script via clasp from the desktop UI (advanced)',
    'EVIDEX_CLASP_CMD=clasp',
    'EVIDEX_GAS_PROJECT_DIR=',
    'LLM_DISABLED=0',
    'SUMMARY_USE_LLM=1',
    'NARRATIVE_USE_LLM=1',
    '',
    '# Local Ollama (OpenAI-compatible) defaults:',
    'OLLAMA_BASE_URL=http://localhost:11434/v1',
    'OLLAMA_MODEL=llama3.2:3b',
    '# If ollama.exe is not on PATH, set an absolute path:',
    '# OLLAMA_EXE_PATH=C:\\Users\\You\\AppData\\Local\\Programs\\Ollama\\ollama.exe',
    '# Optional per-task model overrides:',
    '# OLLAMA_MODEL_SUMMARY=llama3.2:latest',
    '# OLLAMA_MODEL_NARRATIVE=llama3.2:latest',
    '',
    '# Optional Gmail IMAP monitoring (use a Google App Password; paste WITHOUT spaces):',
    'EVIDEX_EMAIL_IMAP_HOST=imap.gmail.com',
    'EVIDEX_EMAIL_IMAP_PORT=993',
    'EVIDEX_EMAIL_IMAP_USER=evidex.ops@gmail.com',
    'EVIDEX_EMAIL_IMAP_PASSWORD=',
    ''
  ) | Set-Content -Encoding UTF8 $envFile
  Write-Host "Created $envFile"
}

$root = Join-Path $repo '_watch_root'
@('incoming','processing','done','failed','deliveries') | ForEach-Object {
  New-Item -ItemType Directory -Force -Path (Join-Path $root $_) | Out-Null
}
Write-Host "Ensured watch root folders: $root"

Write-Host "Done. Launch the UI with: powershell -ExecutionPolicy Bypass -File .\run_ui.ps1"