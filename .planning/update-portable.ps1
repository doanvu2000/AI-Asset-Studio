# Copies the latest unpacked build into the portable folder once nothing from it is running.
# MCP servers (Claude Code / Codex sessions) run the portable studio-core.exe and lock resources\core,
# so close those sessions and the app first, or leave this waiting with -Wait.
param([switch]$Wait)

$src = 'L:\AI\AI-Asset-Studio\app\release\1.0.0\win-unpacked'
$dst = 'L:\AI\AI Asset Studio Portable'

function Busy { @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "$dst\*" }) }

while ((Busy).Count) {
  if (-not $Wait) { Write-Host "Still running from portable:"; Busy | Select-Object ProcessId, Name | Format-Table; exit 1 }
  Start-Sleep -Seconds 60
}
robocopy $src $dst /E /R:2 /W:2 /NFL /NDL /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Host "robocopy failed: $LASTEXITCODE"; exit $LASTEXITCODE }
Write-Host "Portable updated $(Get-Date -Format s)"
