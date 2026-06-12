# github-ado-sync

One-way, **filtered** mirror of the public GitHub Nexus repo into a private Azure DevOps repo. History is rewritten in transit so that:

- **Only the files required to run Nexus reach ADO.** The allowlist lives in [`ado-paths.txt`](ado-paths.txt); anything not listed never appears in ADO — not in the working tree, not in any commit.
- **Personal identities are stripped from history.** Every commit's author and committer are replaced with your org identity per [`ado-mailmap.txt`](ado-mailmap.txt).
- **Org-private commits in ADO can never leak back to GitHub** (physical-separation guarantees below).

Designed for a recurring (e.g. weekly) cadence: the rewrite is deterministic, so each run reproduces identical SHAs for history already in ADO and only the new commits move.

## When to use this

- You maintain Nexus on a public personal GitHub repo.
- Your team works on a fork of it in a private ADO repo owned by your org.
- You periodically pull upstream GitHub changes into the team's ADO fork **without ever pushing org-private code back to GitHub**, without leaking personal docs/notes, and without your personal name in the org's git history.

## How a sync run works

```
GitHub/main
   │ git pull                                  (Nexus-public — push disabled)
   ▼
Nexus-public ──clone──► Nexus-rewrite.git      (scratch mirror, deleted after)
                            │ git filter-repo
                            │   --paths-from-file ado-paths.txt   (file allowlist)
                            │   --mailmap        ado-mailmap.txt  (identity rewrite)
                            ▼
                       bundle file ──fetch──► Nexus-ado ──push──► ADO/upstream-main ──PR──► ADO/main
```

Because `git filter-repo` is deterministic (same input history + same config ⇒ same output SHAs), the weekly run is incremental: the rewritten history lands on top of what ADO already has, and only new commits are pushed. Commits that only touched excluded files are pruned entirely.

**The flip side:** editing `ado-paths.txt` or `ado-mailmap.txt` for paths/identities that already exist in history changes *every* SHA, force-resets `upstream-main` to an unrelated history, and makes the next PR into the team's `main` a mess. Get the config right once; afterwards, only *add* entries for genuinely new files (which is always safe). Both files carry this warning in their headers.

## How the safety guarantee works

Two physically separate clones live side-by-side on your laptop:

```
<BaseDir>\
├── Nexus\           ← (this repo — wherever you cloned it; not used by the scripts)
├── Nexus-public\    ← clone of GitHub. Push URL = "DISABLED_NO_PUSH_TO_PUBLIC". Pre-push hook blocks pushes.
└── Nexus-ado\       ← clone of ADO.    Has no GitHub URL anywhere in `git remote -v`.
```

The two clones share no remotes. Changes move from `Nexus-public` to `Nexus-ado` via a one-off **git bundle file** (created from the rewritten scratch mirror) that the script creates, fetches from, and deletes. There is no live network path between them, so a mistyped command cannot push ADO content to GitHub.

The sync script performs pre-flight checks and **refuses to run** if:
- `Nexus-public` push URL isn't disabled, or
- `Nexus-ado` has any remote URL containing `github.com`, or
- `git filter-repo` is missing, or
- `ado-mailmap.txt` still contains the `ORG-CHANGE-ME` placeholder.

## Branch model in ADO

| Branch | Who writes to it | Purpose |
|---|---|---|
| `upstream-main` | Only you (the sync operator) | Rewritten mirror of GitHub `main`. Force-push allowed. |
| `main` | The team | Real working branch. Receives GitHub updates via PR from `upstream-main`. Never force-pushed. |

GitHub updates flow: `GitHub/main` → rewrite → bundle → ADO/`upstream-main` → PR → ADO/`main`. Conflicts between upstream and team changes surface in the PR.

## The two config files

- **`ado-paths.txt`** — one path per line; a bare directory name includes everything under it; `#` comments and blank lines are ignored. This is the *complete* definition of what flows to ADO. The sync prints which top-level GitHub paths are being held back on every run, so a new run-critical file missing from the list gets noticed rather than silently dropped.
- **`ado-mailmap.txt`** — standard git mailmap lines (`New Name <new@email> Old Name <old@email>`), applied to both author and committer of every commit. **You must replace `ORG-CHANGE-ME` with your org display name and email before the first run.**

Note: rewriting commits necessarily drops GitHub's GPG/“Verified” signatures — expected, since the commits are no longer the originals.

## Prerequisites

- Git for Windows (includes Git Credential Manager).
- PowerShell 5.1+ or PowerShell 7.
- **`git filter-repo`** — `pip install git-filter-repo` (any Python 3; you already have one for the backend).
- Optional: `az` CLI with `azure-devops` extension (only needed if you pass `-CreatePR`):
  ```powershell
  az extension add --name azure-devops
  az devops configure --defaults organization=https://dev.azure.com/<org> project=<project>
  ```
- An **empty** repo created in ADO before first run (no README, no initial commit).

## One-time setup

1. Edit `ado-mailmap.txt` — replace `ORG-CHANGE-ME` with your org identity.
2. Review `ado-paths.txt` — confirm the allowlist matches what your org should receive.
3. From this folder:

```powershell
.\setup-mirror.ps1 `
  -GitHubUrl "https://github.com/<you>/Nexus.git" `
  -AdoUrl    "https://dev.azure.com/<org>/<project>/_git/Nexus"
```

By default the clones are created at `<grandparent of this folder>\Nexus-public` and `\Nexus-ado` — for the typical `E:\Work\MyProjects\Nexus\Scripts\github-ado-sync\` layout, that's `E:\Work\MyProjects\Nexus-public` and `E:\Work\MyProjects\Nexus-ado`. Override with `-BaseDir <path>` if you want them elsewhere.

What it does:
1. Clones GitHub → `Nexus-public`; sets push URL to `DISABLED_NO_PUSH_TO_PUBLIC`; installs a pre-push hook that blocks all pushes.
2. Rewrites the history (allowlist + mailmap) into a scratch mirror and pushes **the rewritten history** to ADO — the raw history never leaves your machine. Refuses if ADO already holds different (e.g. pre-rewrite) history.
3. Clones ADO → `Nexus-ado`.
4. Creates `upstream-main` branch on ADO from `main`.

The script is idempotent — safe to re-run; existing clones are skipped.

> **Migrating from the old un-rewritten mirror?** If you previously bootstrapped ADO with this tool's earlier version, the ADO repo contains the original history (your personal name, all files) in every commit — and a force-push does *not* delete old commits server-side. Delete and recreate the ADO repo as empty, delete the local `Nexus-ado` folder, and re-run `setup-mirror.ps1`. There is nothing to preserve: the rewritten history shares no SHAs with the old one anyway.

### After setup: configure ADO branch policies (manual, one-time)

In the ADO web UI → Repos → Branches:

- **`main`** — add a branch policy: require PR, require 1+ reviewer, **block force-push**. Build validation if you have it.
- **`upstream-main`** — Security tab → set "Contribute" to **Deny** for everyone except your account. Force-push is allowed; only you should touch it.

The script can't reliably set these (policy schemas vary across ADO orgs), but they're a one-time click.

## Recurring sync (weekly, or whenever GitHub updates)

```powershell
.\sync-github-to-ado.ps1                # interactive; open the PR manually in ADO
.\sync-github-to-ado.ps1 -CreatePR      # also opens the ADO PR via az CLI
.\sync-github-to-ado.ps1 -NoConfirm     # unattended (e.g. scheduled weekly task)
```

What it does:
1. Pre-flight checks (refuses to run if the safety setup or rewrite config is broken).
2. `git pull` GitHub into `Nexus-public`; prints which top-level paths are being held back from ADO.
3. Rewrites history into a scratch mirror (`git filter-repo`), bundles it, deletes the scratch.
4. `git fetch <bundle>` in `Nexus-ado` → resets `upstream-main` to match the rewritten GitHub history.
5. `git push origin upstream-main --force-with-lease` + tags.
6. Optionally opens a PR `upstream-main` → `main` via `az repos pr create`.
7. Deletes the bundle file.

If GitHub has no new commits (in allowlisted paths), the rewrite reproduces the SHA already on `upstream-main`; the script detects that, skips the push/PR, and exits cleanly.

### Then in ADO: review and merge the PR

- Resolve any conflicts with team changes (in the ADO web editor for small ones, or locally on `upstream-main` for larger ones).
- Merge with **"Merge (no fast-forward)"** so the rewritten commits survive unchanged on `main`. Don't squash or rebase — that re-rewrites history and breaks the next sync's lineage.

## Parameter reference

Both scripts share these defaults (override any of them with `-Name <value>`):

| Parameter | Default | Meaning |
|---|---|---|
| `BaseDir` | grandparent of this folder | Where `Nexus-public` and `Nexus-ado` live |
| `PublicDirName` | `Nexus-public` | Folder name for the GitHub-facing clone |
| `AdoDirName` | `Nexus-ado` | Folder name for the ADO-facing clone |
| `BranchName` | `main` | The main branch on both remotes |
| `UpstreamBranchName` | `upstream-main` | The mirror branch in ADO (never touched by the team) |
| `PathsFile` | `.\ado-paths.txt` | Allowlist of paths that flow to ADO |
| `MailmapFile` | `.\ado-mailmap.txt` | Identity rewrite map |

`setup-mirror.ps1` also requires `-GitHubUrl` and `-AdoUrl`.
`sync-github-to-ado.ps1` also accepts `-CreatePR` and `-NoConfirm`.

## When something goes wrong

| Symptom | What it means | Fix |
|---|---|---|
| `ado-mailmap.txt still contains the ORG-CHANGE-ME placeholder` | Mailmap was never filled in. | Edit `ado-mailmap.txt` with your org name/email. |
| `git filter-repo not found` | Prerequisite missing. | `pip install git-filter-repo`. |
| `ADO repo ... is not empty and its main does not match` (setup) | ADO holds foreign history — likely the old un-rewritten mirror. | See the migration note above: recreate the ADO repo as empty and re-run. |
| Every SHA changed / `upstream-main` reset to unrelated history | Someone edited `ado-paths.txt` or `ado-mailmap.txt` for paths/identities already in history. | Expected consequence — coordinate with the team; the next PR will be large. Revert the config edit if it was accidental. |
| A needed file isn't in ADO | It's not in `ado-paths.txt` (the sync prints held-back top-level paths each run). | Add it to `ado-paths.txt`. Safe if the file is new on GitHub; history-rewriting if it's old. |
| `Nexus-public fetch URL is not GitHub` | The public clone has been re-pointed. | `git remote set-url origin <github-url>` in `Nexus-public`. |
| `Nexus-public push URL is not DISABLED` | Someone re-enabled push. | Re-run `setup-mirror.ps1` (it'll re-disable). |
| `Nexus-ado has a GitHub URL on remote 'X'` | A GitHub remote got added to the ADO clone. | `git remote remove X` in `Nexus-ado`. **Do not** proceed until clean. |
| Force-with-lease rejected on `upstream-main` push | Someone pushed to `upstream-main` directly. | Investigate — was it you from a different machine? Reconcile manually before re-running. |
| Merge conflict in the PR | Team and upstream changed the same file. | Resolve in the PR (web editor or locally on `upstream-main`). |
| Tag conflict on push | A tag was rewritten differently (config change) or ADO grew its own tag with the same name. | Delete/rename the ADO-side tag before re-running. |

## Want to contribute your own work back to GitHub?

Do it from `Nexus-public` (which has the safety rails off only for explicit, intentional pushes):

```powershell
cd <BaseDir>\Nexus-public
git remote set-url --push origin https://github.com/<you>/Nexus.git
git push origin my-public-feature-branch
git remote set-url --push origin DISABLED_NO_PUSH_TO_PUBLIC
```

The deliberate friction is the feature — it forces a conscious choice every time something goes public. **Never** cherry-pick from `Nexus-ado` into `Nexus-public` (too easy to grab a teammate's commit by mistake — and ADO-side commits must never reach GitHub).
