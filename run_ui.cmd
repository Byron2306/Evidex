@echo off
setlocal
cd /d %~dp0

set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" (
  echo Missing .venv. Creating it...
  python -m venv .venv
)

"%PY%" -m pip install -r requirements.txt
"%PY%" -m evidence_pack_engine.desktop
