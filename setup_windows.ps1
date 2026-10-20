$ErrorActionPreference = "Stop"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher not found. Install 64-bit Python 3.12 first."
}

& py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v

if (-not (Test-Path collector_config.json)) {
    Copy-Item collector_config.example.json collector_config.json
}

Write-Host "Setup and tests completed. Tesseract is resolved from PATH or standard install locations; configure its full path if needed."
Write-Host "Next: edit collector_config.json, then follow WINDOWS_SETUP.md."
