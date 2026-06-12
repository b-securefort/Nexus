<#
.SYNOPSIS
  One-time setup: create read-only Nexus-public (GitHub) and writable Nexus-ado (ADO) clones.

.DESCRIPTION
  Creates two physically separate clones so org-private commits in ADO cannot leak to public GitHub.
    - Nexus-public has its push URL disabled and a pre-push hook that blocks pushes.
    - Nexus-ado has no GitHub URL configured at all.
  Bootstraps the ADO repo by pushing a REWRITTEN copy of GitHub history (only paths in
  ado-paths.txt; identities replaced per ado-mailmap.txt), then creates an `upstream-main`
  tracking branch for future team-collaboration sync PRs.

  Requires git filter-repo (pip install git-filter-repo) and a filled-in ado-mailmap.txt;
  refuses to run while the ORG-CHANGE-ME placeholder is present. The ADO repo must be
  empty (or already hold this rewritten history) - raw pre-rewrite history is never pushed.

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
  [string]$UpstreamBranchName  = "upstream-main",
  [string]$PathsFile           = "$PSScriptRoot\ado-paths.txt",
  [string]$MailmapFile         = "$PSScriptRoot\ado-mailmap.txt"
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\rewrite-lib.ps1"

# Fail early (before any cloning) if the rewrite config isn't ready.
Assert-RewriteConfig -PathsFile $PathsFile -MailmapFile $MailmapFile

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

# --- Nexus-ado: bootstrap REWRITTEN history into ADO, then clone ---
if (-not (Test-Path $adoPath)) {
  Write-Host "[4/5] Bootstrapping ADO with rewritten GitHub history (filtered paths, org identity)" -ForegroundColor Yellow
  $scratch = Join-Path $BaseDir "Nexus-rewrite"
  New-RewrittenMirror -SourcePath $publicPath -ScratchPath $scratch `
                      -PathsFile $PathsFile -MailmapFile $MailmapFile
  try {
    $rewrittenMain = (git -C $scratch rev-parse "refs/heads/$BranchName").Trim()
    $remoteMainRef = git ls-remote $AdoUrl "refs/heads/$BranchName"
    $remoteMain    = if ($remoteMainRef) { ($remoteMainRef -split "`t")[0].Trim() } else { $null }

    if ($remoteMain -and $remoteMain -ne $rewrittenMain) {
      throw ("ADO repo at $AdoUrl is not empty and its $BranchName does not match the rewritten history. " +
             "If it holds a pre-rewrite mirror (original author names, unfiltered files), delete and " +
             "recreate the ADO repo as empty, then re-run. If it holds rewritten history that is just " +
             "behind GitHub, clone it manually and use sync-github-to-ado.ps1 instead.")
    }
    if ($remoteMain) {
      Write-Host "       ADO already holds this rewritten history, skipping push" -ForegroundColor Green
    } else {
      Push-Location $scratch
      try {
        git remote add ado $AdoUrl
        git push ado --all
        git push ado --tags
      } finally { Pop-Location }
    }
  } finally {
    Remove-Item -Recurse -Force $scratch
  }

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
