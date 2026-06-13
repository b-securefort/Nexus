# Azure Engineer skill — tools hardening backlog

Step-by-step review of the 24 tools in the **Azure Engineer** (`chat-with-kb`)
skill. Started 2026-06-12. Reviewed all tools, produced this backlog, doing one
item at a time via `/grill-with-docs` (one design decision before code).

Pattern established by #12–#18 (reuse for the rest): tools own their facts via
**duck-typed `Tool` hooks** (`risk_floor`, `render_for_review`) resolved through
`get_tool()` in `app/agent/risk_review.py` — **NO core→bundle import**, the
bundle→core arrow stays intact. See DESIGN.md §5 2026-06-12 (four entries).

Status legend: ☐ open · ☑ done · ⊘ demoted

---

## Security (internal-only hosting does NOT defuse #1–3, 16, 17, 19, 20, 23)

- ☑ **#1** `az_cli` shell=True-on-Windows + full `os.environ.copy()` → now runs
  through the shared shell=False runner with the §5 2026-05-21 env allowlist
  (`_az_env()` lifted module-level in `_az_base`). NOT routed through `_run_az`
  (can't stream; inheritance would flip `requires_credentials`/ARM-preflight
  semantics; az_cli's Error-prefixed exit-code+stdout contract feeds
  retry/learning). Both `execute()` paths collapsed into draining
  `execute_streaming()` — the orchestrator only dispatches streaming, so the
  old `execute()` 60s timeout was dead code. DONE 2026-06-13 (§5 ×2).
- ☑ **#2** No real streaming timeout → wall-clock `ProcessWatchdog` in core
  `base.py` kills the process tree at the deadline (blocked read hits EOF, the
  generator unwinds); a flag distinguishes timeout-kill (retryable error) from
  Stop-kill (terminal, §5 2026-06-04). Shared `stream_subprocess()` runner
  adopted by az_cli AND execute_script. Timeout still fixed 60s — see #11.
  DONE 2026-06-13.
- ☑ **#3** az_cli invisible to Stop → its `Popen` now registers in the
  per-conversation kill registry via `stream_subprocess`. Reframed during
  design: this is kill-as-resource-hygiene (free the pinned executor thread +
  semaphore slot), NOT kill-as-undo — the §5 2026-06-04 "killing local az
  can't recall an ARM dispatch" rejection still stands; the new §5 entry
  refines it. DONE 2026-06-13.
- ☑ **#12** Remote-exec az commands (`vm run-command invoke`, `aks command
  invoke`, `container/containerapp exec`, `webapp ssh`, `ssh vm/arc`, `acr
  run/build`, vmss variants) → floor ⛔ via `AzCliTool.risk_floor` +
  `_REMOTE_EXEC_PREFIXES` in `bundles/azure/az_cli.py`. Floored not blocked (RCE
  on user's own resource as their own ARM token). DONE 2026-06-12.
- ☑ **#13** Risk reviewer reads resolved `az_rest_api` bodies (was blind to
  `body_file`) via `render_for_review`; oversized mutation body → ⛔. DONE.
- ☑ **#14** `execute_script` review window 4000→16 KB + marker; over-window
  script → ⛔ (closed append-after-truncation). DONE.
- ⊘ **#15** `network_test` SSRF → demoted to low/cleanup: internal-only deployment
  AND port_check/dns_lookup are TCP-connect/DNS only (no HTTP body) so IMDS-token
  theft isn't reachable (that's a web_fetch concern, already guarded). Residual:
  misleading all-ports "allowlist" comment + autonomous internal scan via prompt
  injection. DO NOT reuse web_fetch's RFC1918 block — network_test EXISTS to probe
  private hosts.
- ☐ **#16** Secret-reading commands floor SAFE and are stored plaintext:
  `storage account keys list` / `keyvault secret show` (list/list-keys are read
  verbs → SAFE in `deterministic_floor`), output written verbatim to the
  `messages` table. Fix: treat credential-reads as caution + redact secret shapes
  before persistence.
- ☐ **#17** Floor's "any read verb ⇒ SAFE" shortcut is unsound; destructive token
  set too narrow — `role assignment delete` blocked but `create` (grant self
  Owner) only caution; `stop`/`deallocate` + security-disabling `update`s not
  flagged. (Partially mitigated by #12's `risk_floor` pattern — extend it.)
- ☑ **#18** Human approval card shows backend-rendered command (not a pointer) +
  download for >64 KB. `rendered_command`/`command_truncated` on the SSE event;
  `render_for_human` (64 KB) / `render_command_full` (uncapped); new
  `GET /api/approvals/{id}/command`. DONE 2026-06-12. (Reason-line on the card was
  reverted — contradicts "reason is audit-only" decision, models.py:103.)
- ☐ **#19** Identity confusion — `execute_script`'s `az` calls run as the SERVER's
  `az login`, not the user's ARM token (`_shell_env` omits `AZURE_ACCESS_TOKEN`),
  while `az_cli` injects it. Privilege-escalation + audit-attribution gap. NEEDS A
  DESIGN DECISION before code.
- ☐ **#20** TOCTOU between review and execution — reviewer reads script body at
  approval time, executor re-reads from disk at run time. Cheap fix: hash the
  reviewed bytes on the approval, re-check before execute.
- ☐ **#23** No structured audit log of executed mutations — no immutable record of
  the *resolved* command/body that ran, who approved, and outcome.
- ☐ **@file indirection** (spun out of #12): `az vm run-command invoke --scripts
  @output/x.ps1` — reviewer/card see `@file`, not content. Same render-resolution
  gap as #13's body_file; fold into the render_for_review resolution.

## Functional gaps

- ☐ **#4** `az_devops` weak for a pipeline-heavy engineer: no run logs/timeline
  (show_build is metadata only — highest value add), list_builds can't filter by
  pipeline/branch/status/result, trigger_build takes no `--parameters`, no
  cancel_build, create_pr has no description/reviewers.
- ☐ **#5** `az_rest_api` rejects multi-query-param URLs — `check_shell_injection`
  blocks `&` and `%`, so `?api-version=…&$filter=…` or `%20` fails. Once #1 makes
  things genuinely shell=False, exempt/structurally-validate the URL arg.
- ☐ **#6** `az_resource_graph` silently caps at 100 records (`--first 100`
  hardcoded, no pagination/skip-token). az supports `--first` up to 1000.
- ☐ **#7** `az_cost_query` "forecast" doesn't forecast — posts `type: Usage`,
  MonthToDate (= month-to-date actuals, mislabeled). Real endpoint is
  `/providers/Microsoft.CostManagement/forecast`.
- ☐ **#8** `network_test` does less than advertised: promises "effective NSG rules
  for a NIC/subnet" but lists a named NSG's rules. Missing `effective_nsg` (per
  NIC) + `effective_routes` (the spoke "is traffic forced through the hub
  firewall" answer). Also note results originate from the Nexus server's vantage.
- ☐ **#9** `az_monitor_logs` auto-discovery silently picks the FIRST workspace —
  in a multi-workspace sub, queries can hit the wrong one and report a confident
  "0 results". At minimum name the workspace used; better: list when multiple.
- ☐ **#10** `execute_script` can't pass arguments to scripts — changing one value
  means regenerating the whole file. A bounded `args: list[str]` (shell=False,
  shown on the approval card) keeps the no-inline-command property.
- ☐ **#11** Fixed timeouts too short for writes — az_cli hard-capped 60s,
  execute_script 120s; a VM create/deployment fails on time even when succeeding.
  Add a bounded `timeout_seconds` to az_cli, or teach `--no-wait` + poll.
- ☐ **#21** `az_advisor` / `az_policy_check` can't scope to a subscription (only
  `az_cost_query` takes `subscription`) — always hit the default sub. Same class
  as #9.
- ☐ **#22** Fail-open tool config — `init_tools` enables any tool whose
  `config_flag` setting is missing (`getattr(settings, flag, True)`), so a typo'd
  flag leaves a tool silently always-on. Default-deny for security-sensitive
  tools.

## Minor polish (unprioritized)

- az_cli & execute_script truncate output at 8 KB (`_MAX_OUTPUT_SIZE`) while
  declaring `result_limit = 12_000` — cut 4 KB earlier than allowed.
- `fetch_ms_docs` hardcodes `scope: "Azure"` — DevOps YAML / PowerShell docs may
  rank poorly. Make scope an optional param.
- `az_policy_check` returns one record per policy×resource (dupes); no action
  answers "which assignment denied my deployment".
- `read_file` has no offset/chunk support (its own comment anticipates it).
- In-good-shape, no change pushed: KB tools, web_search, web_fetch,
  search_github/stackoverflow/azure_updates, search_conversation, sleep.

---

## Verified baseline at handoff (2026-06-12)

`#12/#13/#14/#18` committed to the **working tree** (uncommitted, ~781 insertions
across 13 files). Backend `pytest tests/`: **1153 passed**. Frontend: `tsc`
clean, **157 vitest passed**. Suggested next: #1–3 as a single "az_cli hardening"
change (shares the `_run_az` template); or #19 (needs the identity decision
first). See memory `project_engineer_tools_security_review`.
