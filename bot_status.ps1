# bot_status.ps1 - Check if the OpenBot Telegram instance is alive, and whether
# it is running the current code (commit matches HEAD).
#
# Usage (PowerShell):  .\bot_status.ps1
#
# Exit codes: 0 = alive & up to date, 1 = alive but stale, 2 = not running.

$ErrorActionPreference = "SilentlyContinue"

$pidFile = Join-Path $PSScriptRoot ".bot.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "DETENIDO - no existe .bot.pid (el bot no esta corriendo o no escribio el archivo)"
    exit 2
}

$lines = Get-Content $pidFile
$pid = $lines[0].Trim()
$commit = ($lines | Where-Object { $_ -like "commit=*" }) -replace "commit=", ""
$started = ($lines | Where-Object { $_ -like "started=*" }) -replace "started=", ""

$proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "DETENIDO - .bot.pid apunta al PID $pid pero ese proceso no existe (archivo fantasma, borrar .bot.pid)"
    exit 2
}

$head = (& git -C $PSScriptRoot rev-parse --short HEAD 2>$null).Trim()

Write-Host "VIVO - PID $pid"
Write-Host "  Iniciado: $started"
Write-Host "  Commit instancia: $commit"

if ($head -and $commit -and $commit -ne "unknown" -and $commit -ne $head) {
    Write-Host "  DESACTUALIZADO - el codigo en disco es commit $head; reinicia el bot para actualizar."
    exit 1
} elseif ($head) {
    Write-Host "  ACTUALIZADO - coincide con HEAD ($head)."
    exit 0
} else {
    Write-Host "  (No se pudo obtener HEAD de git; no se puede comparar version.)"
    exit 0
}
