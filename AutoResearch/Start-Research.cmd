@echo off
cd /d "%~dp0"
python -c "import jsonschema"
if errorlevel 1 (
  echo Install dependencies first: python -m pip install -r requirements.txt
  pause
  exit /b 1
)
python -m experiments.mission_flow.auto_information --output research/information-001 --rounds 40 --patience 8 --proposals 3 --screen 24 --validate 64 --final 128 --workers 4
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
