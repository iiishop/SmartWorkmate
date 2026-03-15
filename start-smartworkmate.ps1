param(
  [Parameter(Position=0)]
  [string]$Mode = "execute",

  [Parameter(Position=1)]
  [string]$User = "iiishop"
)

function Zh([int[]]$codes) {
  return -join ($codes | ForEach-Object { [char]$_ })
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$zhUsage = Zh @(0x7528, 0x6CD5)
$zhExamples = Zh @(0x793A, 0x4F8B)
$zhUser = Zh @(0x7528, 0x6237, 0x540D)

if ($Mode -in @("help", "--help")) {
  Write-Host "$zhUsage / Usage:"
  Write-Host "  start-smartworkmate.bat [dry-run|--dry-run] [$zhUser]"
  Write-Host ""
  Write-Host "$zhExamples / Examples:"
  Write-Host "  start-smartworkmate.bat"
  Write-Host "  start-smartworkmate.bat dry-run"
  Write-Host "  start-smartworkmate.bat dry-run iiishop"
  exit 0
}

$isDryRun = $Mode -in @("dry-run", "--dry-run")

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "[ERROR] uv not found in PATH."
  Write-Host "[TIP] Install uv first, then rerun this script."
  exit 1
}

Write-Host "[INFO] Repo root: $repoRoot"
Write-Host "[INFO] Syncing dependencies (uv sync)..."
uv sync
if ($LASTEXITCODE -ne 0) {
  Write-Host "[ERROR] uv sync failed."
  exit 1
}

if ($isDryRun) {
  Write-Host "[INFO] Starting SmartWorkmate in dry-run once mode..."
  uv run python -m smartworkmate.cli start --root "$repoRoot" --dry-run --once --user "$User"
} else {
  Write-Host "[INFO] Starting SmartWorkmate in execute mode..."
  uv run python -m smartworkmate.cli start --root "$repoRoot" --execute --user "$User"
}

if ($LASTEXITCODE -ne 0) {
  Write-Host "[ERROR] SmartWorkmate exited with an error."
  exit 1
}

Write-Host "[INFO] SmartWorkmate finished."
exit 0
