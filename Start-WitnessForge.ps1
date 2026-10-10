$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m witnessforge serve --port 8765
