<#
  Shared rewrite step for setup-mirror.ps1 and sync-github-to-ado.ps1.

  Produces a scratch mirror of the public clone with history rewritten for ADO:
    - only paths listed in ado-paths.txt survive (git filter-repo --paths-from-file)
    - author/committer identities replaced per ado-mailmap.txt (--mailmap)

  The rewrite is deterministic: same input history + same config files always
  yield the same rewritten SHAs. That is the property that makes the weekly
  sync incremental - each run re-derives the history already in ADO and only
  the new commits move.
#>

function Get-AllowedPaths {
  param([Parameter(Mandatory)][string]$PathsFile)
  Get-Content $PathsFile |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith('#') }
}

function Assert-RewriteConfig {
  param(
    [Parameter(Mandatory)][string]$PathsFile,
    [Parameter(Mandatory)][string]$MailmapFile
  )
  if (-not (Test-Path $PathsFile))   { throw "Allowlist not found: $PathsFile" }
  if (-not (Test-Path $MailmapFile)) { throw "Mailmap not found: $MailmapFile" }

  if (-not (Get-AllowedPaths $PathsFile)) {
    throw "$PathsFile has no paths - the rewrite would delete the entire history."
  }
  if (Select-String -Path $MailmapFile -Pattern 'ORG-CHANGE-ME' -Quiet) {
    throw "$MailmapFile still contains the ORG-CHANGE-ME placeholder. Fill in your org name/email first."
  }

  git filter-repo --version *> $null
  if ($LASTEXITCODE -ne 0) {
    throw "git filter-repo not found. Install it once with: pip install git-filter-repo"
  }
}

function New-RewrittenMirror {
  <#
    Clones $SourcePath (the Nexus-public clone) into a fresh scratch clone at
    $ScratchPath and runs git filter-repo on it. Caller bundles/pushes from
    the scratch clone and deletes it afterwards.

    A regular (non-bare) clone is used deliberately: a bare --mirror clone
    breaks under git's safe.bareRepository=explicit hardening, which
    filter-repo's own git subprocesses don't override. filter-repo turns the
    clone's refs/remotes/origin/* into local branches as part of the rewrite,
    so all branches and tags survive either way.
  #>
  param(
    [Parameter(Mandatory)][string]$SourcePath,
    [Parameter(Mandatory)][string]$ScratchPath,
    [Parameter(Mandatory)][string]$PathsFile,
    [Parameter(Mandatory)][string]$MailmapFile
  )

  Assert-RewriteConfig -PathsFile $PathsFile -MailmapFile $MailmapFile

  if (Test-Path $ScratchPath) { Remove-Item -Recurse -Force $ScratchPath }
  # --no-local: behave like a network clone so filter-repo sees a fresh repo.
  git clone --no-local --quiet $SourcePath $ScratchPath
  if ($LASTEXITCODE -ne 0) { throw "Clone of $SourcePath failed." }

  # filter-repo wants clean input files: hand it copies with comments/blank
  # lines stripped and LF endings (CRLF would end up inside the path names).
  $tmpPaths   = Join-Path ([IO.Path]::GetTempPath()) 'ado-paths-clean.txt'
  $tmpMailmap = Join-Path ([IO.Path]::GetTempPath()) 'ado-mailmap-clean.txt'
  $cleanLines = {
    param($file)
    (Get-Content $file | ForEach-Object { $_.Trim() } |
      Where-Object { $_ -and -not $_.StartsWith('#') }) -join "`n"
  }
  [IO.File]::WriteAllText($tmpPaths,   (& $cleanLines $PathsFile)   + "`n")
  [IO.File]::WriteAllText($tmpMailmap, (& $cleanLines $MailmapFile) + "`n")

  Push-Location $ScratchPath
  try {
    git filter-repo --quiet --paths-from-file $tmpPaths --mailmap $tmpMailmap
    if ($LASTEXITCODE -ne 0) { throw "git filter-repo failed in $ScratchPath." }
  } finally {
    Pop-Location
    Remove-Item -Force $tmpPaths, $tmpMailmap -ErrorAction SilentlyContinue
  }
}
