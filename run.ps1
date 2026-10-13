$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать окружение Python 3.11" }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }
& .\.venv\Scripts\python.exe -m baknin.cli init
if ($LASTEXITCODE -ne 0) { throw "Не удалось подготовить базу" }
& .\.venv\Scripts\python.exe app.py
exit $LASTEXITCODE
