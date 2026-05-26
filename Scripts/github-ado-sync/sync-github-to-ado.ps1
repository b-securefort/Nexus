<#
.SYNOPSIS
  Recurring sync: pull GitHub into Nexus-public, transfer via bundle to Nexus-ado, push to ADO upstream-main.

.DESCRIPTION
  Safe one-way sync from public GitHub to private ADO.
    1. Pre-flight: verifies Nexus-public has push DISABLED and Nexus-ado has no GitHub URL.
    2. Pulls GitHub into Nexus-public.
    3. Bundles refs into a single file.
    4. Imports the bundle into Nexus-ado and force-updates `upstream-main` to match.
    5. Pushes `upstream-main` + tags to ADO.
    6. Optionally opens a PR (upstream-main -> main) via `az repos pr create`.

  The bundle file is the only bridge; no remote ever links the two clones.
  See README.md in this folder for full context.

.EXAMPLE
  .\sync-github-to-ado.ps1                # interactive, manual PR
  .\sync-github-to-ado.ps1 -CreatePR      # also opens an ADO PR (needs az CLI + azure-devops ext)
  .\sync-github-to-ado.ps1 -NoConfirm     # for scheduled / unattended runs
#>
[CmdletBinding()]
param(
  # Default assumes scripts live at <BaseDir>\Nexus\Scripts\github-ado-sync\ and clones go in <BaseDir>\.
  [string]$BaseDir = (Resolve-Path "$PSScriptRoot\..\..\..").Path,
  [string]$PublicDirName       = "Nexus-public",
  [string]$AdoDirName          = "Nexus-ado",
  [string]$BranchName          = "main",
  [string]$UpstreamBranchName  = "upstream-main",
  [switch]$CreatePR,
  [switch]$NoConfirm
)

$ErrorActionPreference = 'Stop'

$publicPath = Join-Path $BaseDir $PublicDirName
$adoPath    = Join-Path $BaseDir $AdoDirName
$bundlePath = Join-Path $BaseDir "nexus-sync.bundle"

# ---------- Pre-flight ----------
Write-Host ""
Write-Host "=== Pre-flight checks ===" -ForegroundColor Cyan

if (-not (Test-Path $publicPath)) { throw "Public clone not found: $publicPath. Run setup-mirror.ps1 first." }
if (-not (Test-Path $adoPath))    { throw "ADO clone not found: $adoPath. Run setup-mirror.ps1 first." }

Push-Location $publicPath
try {
  $pubFetch = (git remote get-url origin).Trim()
  $pubPush  = (git remote get-url --push origin).Trim()
  if ($pubFetch -notmatch 'github\.com')   { throw "$PublicDirName fetch URL is not GitHub: $pubFetch" }
  if ($pubPush  -notmatch '^DISABLED')     { throw "$PublicDirName push URL is not DISABLED (got: $pubPush). Refusing - see setup-mirror.ps1." }
  Write-Host "  [OK] ${PublicDirName}: fetch=GitHub, push=DISABLED"
} finally { Pop-Location }

Push-Location $adoPath
try {
  $remotes = git remote
  foreach ($r in $remotes) {
    $url = (git remote get-url $r).Trim()
    if ($url -match 'github\.com') {
      throw "$AdoDirName has a GitHub URL on remote '$r' ($url). Refusing - this clone must be ADO-only."
    }
  }
  $adoOrigin = (git remote get-url origin).Trim()
  if ($adoOrigin -notmatch 'dev\.azure\.com|visualstudio\.com') {
    throw "$AdoDirName origin does not look like ADO: $adoOrigin"
  }
  Write-Host "  [OK] ${AdoDirName}: origin=ADO only, no GitHub URLs"
} finally { Pop-Location }

if (-not $NoConfirm) {
  Write-Host ""
  $reply = Read-Host "Proceed with sync (GitHub -> ADO $UpstreamBranchName)? [y/N]"
  if ($reply -notmatch '^[Yy]') { Write-Host "Aborted."; exit 0 }
}

# ---------- Step 1: pull GitHub ----------
Write-Host ""
Write-Host "=== Step 1: pull GitHub into $PublicDirName ===" -ForegroundColor Cyan
Push-Location $publicPath
try {
  git fetch origin --prune --tags
  git checkout $BranchName
  git pull --ff-only origin $BranchName

  # ---------- Step 2: bundle ----------
  Write-Host ""
  Write-Host "=== Step 2: create transport bundle ===" -ForegroundColor Cyan
  if (Test-Path $bundlePath) { Remove-Item -Force $bundlePath }
  git bundle create $bundlePath --all
  $bundleSize = [Math]::Round((Get-Item $bundlePath).Length / 1MB, 2)
  Write-Host "  Bundle: $bundlePath ($bundleSize MB)"
} finally { Pop-Location }

# ---------- Step 3+4: import bundle, update upstream-main ----------
Write-Host ""
Write-Host "=== Step 3: fetch latest ADO state ===" -ForegroundColor Cyan
Push-Location $adoPath
try {
  git fetch origin --prune

  Write-Host ""
  Write-Host "=== Step 4: import bundle into $AdoDirName ===" -ForegroundColor Cyan
  git fetch $bundlePath 'refs/heads/*:refs/remotes/github-import/*' --tags

  Write-Host ""
  Write-Host "=== Step 5: update $UpstreamBranchName to match GitHub/$BranchName ===" -ForegroundColor Cyan
  git checkout $UpstreamBranchName
  $beforeSha = (git rev-parse HEAD).Trim()
  git reset --hard "github-import/$BranchName"
  $afterSha = (git rev-parse HEAD).Trim()

  if ($beforeSha -eq $afterSha) {
    Write-Host "  No new commits - $UpstreamBranchName already matches GitHub/$BranchName." -ForegroundColor Green
    Write-Host "  Skipping push and PR creation."
    git checkout $BranchName
    Remove-Item -Force $bundlePath
    exit 0
  }

  Write-Host "  $UpstreamBranchName moved: $($beforeSha.Substring(0,8)) -> $($afterSha.Substring(0,8))"

  Write-Host ""
  Write-Host "=== Step 6: push $UpstreamBranchName + tags to ADO ===" -ForegroundColor Cyan
  git push origin $UpstreamBranchName --force-with-lease
  git push origin --tags
  git checkout $BranchName
} finally { Pop-Location }

# ---------- Step 7: optional PR ----------
if ($CreatePR) {
  Write-Host ""
  Write-Host "=== Step 7: open PR via az CLI ===" -ForegroundColor Cyan
  if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Warning "az CLI not found. Open the PR manually in the ADO web UI."
  } else {
    Push-Location $adoPath
    try {
      $title = "Sync from upstream GitHub ($(Get-Date -Format 'yyyy-MM-dd'))"
      $desc  = "Routine upstream sync from public GitHub Nexus. Review for conflicts with team changes on $BranchName."
      az repos pr create `
        --source-branch $UpstreamBranchName `
        --target-branch $BranchName `
        --title $title `
        --description $desc `
        --output table
    } finally { Pop-Location }
  }
} else {
  Write-Host ""
  Write-Host "PR not auto-created. Open one in the ADO web UI:" -ForegroundColor Yellow
  Write-Host "  source: $UpstreamBranchName   target: $BranchName"
  Write-Host "(or re-run with -CreatePR if az CLI + azure-devops extension are installed)"
}

# ---------- Cleanup ----------
Remove-Item -Force $bundlePath
Write-Host ""
Write-Host "Sync complete." -ForegroundColor Green
