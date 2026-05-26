# github-ado-sync

One-way mirror of the **public GitHub Nexus** repo into a **private Azure DevOps** repo, with hard guarantees that org-private commits in ADO can never leak back to GitHub.

## When to use this

- You maintain Nexus on a public personal GitHub repo.
- Your team works on a fork of it in a private ADO repo owned by your org.
- You periodically want to pull upstream GitHub changes into the team's ADO fork **without ever pushing org-private code back to GitHub**.

## How the safety guarantee works

Two physically separate clones live side-by-side on your laptop:

```
<BaseDir>\
├── Nexus\           ← (this repo — wherever you cloned it; not used by the scripts)
├── Nexus-public\    ← clone of GitHub. Push URL = "DISABLED_NO_PUSH_TO_PUBLIC". Pre-push hook blocks pushes.
└── Nexus-ado\       ← clone of ADO.    Has no GitHub URL anywhere in `git remote -v`.
```

The two clones share no remotes. Changes move from `Nexus-public` to `Nexus-ado` via a one-off **git bundle file** that the script creates, fetches from, and deletes. There is no live network path between them, so a mistyped command cannot push ADO content to GitHub.

The sync script also performs pre-flight checks and **refuses to run** if:
- `Nexus-public` push URL isn't disabled, or
- `Nexus-ado` has any remote URL containing `github.com`.

## Branch model in ADO

| Branch | Who writes to it | Purpose |
|---|---|---|
| `upstream-main` | Only you (the sync operator) | Exact mirror of GitHub `main`. Force-push allowed. |
| `main` | The team | Real working branch. Receives GitHub updates via PR from `upstream-main`. Never force-pushed. |

GitHub updates flow: `GitHub/main` → bundle → ADO/`upstream-main` → PR → ADO/`main`. Conflicts between upstream and team changes surface in the PR.

## Prerequisites

- Git for Windows (includes Git Credential Manager).
- PowerShell 5.1+ or PowerShell 7.
- Optional: `az` CLI with `azure-devops` extension (only needed if you pass `-CreatePR`):
  ```powershell
  az extension add --name azure-devops
  az devops configure --defaults organization=https://dev.azure.com/<org> project=<project>
  ```
- An **empty** repo created in ADO before first run (no README, no initial commit).

## One-time setup

From this folder:

```powershell
.\setup-mirror.ps1 `
  -GitHubUrl "https://github.com/<you>/Nexus.git" `
  -AdoUrl    "https://dev.azure.com/<org>/<project>/_git/Nexus"
```

By default the clones are created at `<grandparent of this folder>\Nexus-public` and `\Nexus-ado` — for the typical `E:\Work\MyProjects\Nexus\Scripts\github-ado-sync\` layout, that's `E:\Work\MyProjects\Nexus-public` and `E:\Work\MyProjects\Nexus-ado`. Override with `-BaseDir <path>` if you want them elsewhere.

What it does:
1. Clones GitHub → `Nexus-public`; sets push URL to `DISABLED_NO_PUSH_TO_PUBLIC`; installs a pre-push hook that blocks all pushes.
2. Mirror-pushes GitHub history → ADO (one-time bootstrap so the empty ADO repo has full git history with original commit SHAs, authors, dates).
3. Clones ADO → `Nexus-ado`.
4. Creates `upstream-main` branch on ADO from `main`.

The script is idempotent — safe to re-run; existing clones are skipped.

### After setup: configure ADO branch policies (manual, one-time)

In the ADO web UI → Repos → Branches:

- **`main`** — add a branch policy: require PR, require 1+ reviewer, **block force-push**. Build validation if you have it.
- **`upstream-main`** — Security tab → set "Contribute" to **Deny** for everyone except your account. Force-push is allowed; only you should touch it.

The script can't reliably set these (policy schemas vary across ADO orgs), but they're a one-time click.

## Recurring sync (every time GitHub updates)

```powershell
.\sync-github-to-ado.ps1                # interactive; open the PR manually in ADO
.\sync-github-to-ado.ps1 -CreatePR      # also opens the ADO PR via az CLI
.\sync-github-to-ado.ps1 -NoConfirm     # unattended (e.g. scheduled)
```

What it does:
1. Pre-flight checks (refuses to run if the safety setup is broken).
2. `git pull` GitHub into `Nexus-public`.
3. `git bundle create` — packages all refs into a single file.
4. `git fetch <bundle>` in `Nexus-ado` → resets `upstream-main` to match GitHub.
5. `git push origin upstream-main --force-with-lease` + tags.
6. Optionally opens a PR `upstream-main` → `main` via `az repos pr create`.
7. Deletes the bundle file.

If GitHub has no new commits, the script detects that, skips the push/PR, and exits cleanly.

### Then in ADO: review and merge the PR

- Resolve any conflicts with team changes (in the ADO web editor for small ones, or locally on `upstream-main` for larger ones).
- Merge with **"Merge (no fast-forward)"** so the GitHub commit SHAs and original authors survive on `main`. Don't squash or rebase — that rewrites history and breaks the next sync's lineage.

## Parameter reference

Both scripts share these defaults (override any of them with `-Name <value>`):

| Parameter | Default | Meaning |
|---|---|---|
| `BaseDir` | grandparent of this folder | Where `Nexus-public` and `Nexus-ado` live |
| `PublicDirName` | `Nexus-public` | Folder name for the GitHub-facing clone |
| `AdoDirName` | `Nexus-ado` | Folder name for the ADO-facing clone |
| `BranchName` | `main` | The main branch on both remotes |
| `UpstreamBranchName` | `upstream-main` | The mirror branch in ADO (never touched by the team) |

`setup-mirror.ps1` also requires `-GitHubUrl` and `-AdoUrl`.
`sync-github-to-ado.ps1` also accepts `-CreatePR` and `-NoConfirm`.

## When something goes wrong

| Symptom | What it means | Fix |
|---|---|---|
| `Nexus-public fetch URL is not GitHub` | The public clone has been re-pointed. | `git remote set-url origin <github-url>` in `Nexus-public`. |
| `Nexus-public push URL is not DISABLED` | Someone re-enabled push. | Re-run `setup-mirror.ps1` (it'll re-disable). |
| `Nexus-ado has a GitHub URL on remote 'X'` | A GitHub remote got added to the ADO clone. | `git remote remove X` in `Nexus-ado`. **Do not** proceed until clean. |
| Force-with-lease rejected on `upstream-main` push | Someone pushed to `upstream-main` directly. | Investigate — was it you from a different machine? Reconcile manually before re-running. |
| Merge conflict in the PR | Team and upstream changed the same file. | Resolve in the PR (web editor or locally on `upstream-main`). |
| Tag conflict on push | GitHub and ADO both created the same tag name. | Rename one of the tags before re-running. |

## Want to contribute your own work back to GitHub?

Do it from `Nexus-public` (which has the safety rails off only for explicit, intentional pushes):

```powershell
cd <BaseDir>\Nexus-public
git remote set-url --push origin https://github.com/<you>/Nexus.git
git push origin my-public-feature-branch
git remote set-url --push origin DISABLED_NO_PUSH_TO_PUBLIC
```

The deliberate friction is the feature — it forces a conscious choice every time something goes public. **Never** cherry-pick from `Nexus-ado` into `Nexus-public` (too easy to grab a teammate's commit by mistake).
