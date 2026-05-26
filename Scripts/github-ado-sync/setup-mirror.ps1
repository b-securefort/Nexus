<#
.SYNOPSIS
  One-time setup: create read-only Nexus-public (GitHub) and writable Nexus-ado (ADO) clones.

.DESCRIPTION
  Creates two physically separate clones so org-private commits in ADO cannot leak to public GitHub.
    - Nexus-public has its push URL disabled and a pre-push hook that blocks pushes.
    - Nexus-ado has no GitHub URL configured at all.
  Bootstraps the ADO repo by mirror-pushing GitHub history once, then creates an `upstream-main`
  tracking branch for future team-collaboration sync PRs.

  See README.md in this folder for full context and ADO branch-policy steps.

.EXAMPLE
  .\setup-mirror.ps1 `
    -GitHubUrl "https://github.com/<you>/Nexus.git" `
    -AdoUrl    "https://dev.azure.com/<org>/<project>/_git/Nexus" `
    -BaseDir   "E:\Work\MyProjects"
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$GitHubUrl,
  [Parameter(Mandatory)][string]$AdoUrl,
  # Default assumes scripts live at <BaseDir>\Nexus\Scripts\github-ado-sync\ and clones go in <BaseDir>\.
  [string]$BaseDir = (Resolve-Path "$PSScriptRoot\..\..\..").Path,
  [string]$PublicDirName       = "Nexus-public",
  [string]$AdoDirName          = "Nexus-ado",
  [string]$BranchName          = "main",
  [string]$UpstreamBranchName  = "upstream-main"
)

$ErrorActionPreference = 'Stop'

if ($AdoUrl    -match    'github\.com') { throw "AdoUrl looks like GitHub. Refusing." }
if ($GitHubUrl -notmatch 'github\.com') { throw "GitHubUrl does not look like github.com." }

$publicPath = Join-Path $BaseDir $PublicDirName
$adoPath    = Join-Path $BaseDir $AdoDirName

Write-Host ""
Write-Host "=== Setup mirror: GitHub -> ADO ===" -ForegroundColor Cyan
Write-Host "GitHub:        $GitHubUrl"
Write-Host "ADO:           $AdoUrl"
Write-Host "Public clone:  $publicPath"
Write-Host "ADO clone:     $adoPath"
Write-Host ""

# --- Nexus-public: clone, disable push, install pre-push guard ---
if (-not (Test-Path $publicPath)) {
  Write-Host "[1/5] Cloning GitHub -> $PublicDirName" -ForegroundColor Yellow
  git clone $GitHubUrl $publicPath
} else {
  Write-Host "[1/5] $PublicDirName already exists, skipping clone" -ForegroundColor Green
}

Push-Location $publicPath
try {
  Write-Host "[2/5] Disabling push URL on $PublicDirName/origin" -ForegroundColor Yellow
  git remote set-url --push origin DISABLED_NO_PUSH_TO_PUBLIC

  Write-Host "[3/5] Installing pre-push hook in $PublicDirName" -ForegroundColor Yellow
  $hookPath = Join-Path (Get-Location) ".git\hooks\pre-push"
  $hookBody = @"
#!/bin/sh
echo "BLOCKED: pushes from $PublicDirName are disabled. This clone is read-only." >&2
exit 1
"@
  Set-Content -Path $hookPath -Value $hookBody -Encoding ASCII
} finally { Pop-Location }

# --- Nexus-ado: bootstrap history from GitHub, then clone ---
if (-not (Test-Path $adoPath)) {
  Write-Host "[4/5] Bootstrapping ADO from GitHub history (mirror push)" -ForegroundColor Yellow
  $bootstrap = Join-Path $BaseDir "Nexus-bootstrap.git"
  if (Test-Path $bootstrap) { Remove-Item -Recurse -Force $bootstrap }
  git clone --mirror $GitHubUrl $bootstrap
  Push-Location $bootstrap
  try {
    git remote set-url origin $AdoUrl
    git push --mirror origin
  } finally { Pop-Location }
  Remove-Item -Recurse -Force $bootstrap

  Write-Host "       Cloning ADO -> $AdoDirName" -ForegroundColor Yellow
  git clone $AdoUrl $adoPath
} else {
  Write-Host "[4/5] $AdoDirName already exists, skipping bootstrap" -ForegroundColor Green
}

# Verify ADO clone has no GitHub URL anywhere
Push-Location $adoPath
try {
  $adoOrigin = (git remote get-url origin).Trim()
  if ($adoOrigin -match 'github\.com') {
    throw "ADO clone has a GitHub URL set on origin: $adoOrigin. Aborting."
  }

  Write-Host "[5/5] Ensuring $UpstreamBranchName exists on ADO" -ForegroundColor Yellow
  $remoteBranch = git ls-remote --heads origin $UpstreamBranchName 2>$null
  if (-not $remoteBranch) {
    git checkout $BranchName
    git pull --ff-only
    git checkout -B $UpstreamBranchName
    git push -u origin $UpstreamBranchName
    git checkout $BranchName
  } else {
    Write-Host "       $UpstreamBranchName already exists on ADO, skipping"
  }
} finally { Pop-Location }

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps in the ADO web UI:" -ForegroundColor Cyan
Write-Host "  - On '$BranchName': add branch policy -> require PR, block force-push."
Write-Host "  - On '$UpstreamBranchName': restrict push to your account only."
Write-Host ""
Write-Host "To sync GitHub updates into ADO later:"
Write-Host "  .\sync-github-to-ado.ps1"
