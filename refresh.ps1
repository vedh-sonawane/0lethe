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

Write-Host "pushing it live (Cloudflare Pages)..."
# needs: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID set in your environment
npx wrangler@latest pages deploy site --project-name=0lethe --branch=main --commit-dirty=true

Write-Host "done. the world remembers a little more."
