$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
}
if (!(Test-Path '.env')) { & .\.venv\Scripts\python.exe setup_local.py }
Write-Host 'Open API docs at http://127.0.0.1:8000/docs'
& .\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
