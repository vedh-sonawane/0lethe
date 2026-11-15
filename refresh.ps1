# Push the latest state of the machine out to the world.
#   - freezes the current graph into the static field (site/)
#   - deploys it to the live URL
# Run it by hand, or schedule it (Task Scheduler) so the public field grows on
# its own and whoever is watching sees the world remember a little more.
#
#   powershell -ExecutionPolicy Bypass -File refresh.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "freezing the field..."
python -m lethe.cli export site

Write-Host "pushing it live..."
Set-Location (Join-Path $root "site")
vercel --prod --yes

Set-Location $root
Write-Host "done. the world remembers a little more."
