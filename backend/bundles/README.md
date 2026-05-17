# Nexus Bundles

Tool bundles are optional, team-specific extensions. They live here — outside `app/` — so teams that fork Nexus can ignore the bundles that don't apply to them without touching core code.

## Two-layer model

| Directory | What it is | Rule |
|---|---|---|
| `backend/app/` | Core — orchestrator, auth, DB, KB, generic tools | Never modify in a fork |
| `backend/bundles/` | Optional — team-specific tool sets | Add your bundle here; ignore the rest |

## Your skill controls what the agent can call

A bundle being loaded does **not** mean the agent uses those tools. The agent can only call tools listed in the active skill's `tools:` frontmatter. A team that writes a skill without listing `az_cli` can never trigger it — even if the Azure bundle is loaded.

This means a new team can adopt Nexus immediately by writing one `SKILL.md` with only the tools they need. They never have to touch or disable other bundles.

## Disabling a bundle you don't need

Set the flag in `backend/.env`:

```
TOOL_BUNDLE_AZURE_ENABLED=false
```

The bundle's code will not be imported and those tools will not appear in `TOOL_REGISTRY`.

## Adding a new team bundle

1. Create `bundles/<teamname>/` with an `__init__.py`
2. Add your Tool subclasses — auto-registration fires on import via `__init_subclass__`
3. Add `TOOL_BUNDLE_<TEAMNAME>_ENABLED: bool = False` to `backend/app/config.py`
4. Add a loading block in `init_tools()` in `backend/app/tools/base.py`:

```python
if settings.TOOL_BUNDLE_<TEAMNAME>_ENABLED:
    import bundles.<teamname>
    for _, module_name, _ in pkgutil.iter_modules(bundles.<teamname>.__path__):
        if not module_name.startswith("_"):
            importlib.import_module(f"bundles.<teamname>.{module_name}")
```

5. Import from `app.tools.base` for `AzureToolBase` / `Tool`, `check_shell_injection`, etc. — those are stable core APIs.

## What bundles must not do

- Import from other bundles (e.g. `from bundles.azure import ...`) — use `app.tools.base` only
- Write files outside `backend/output/` — the output sandbox invariant applies to all tools
- Set `requires_approval=False` on tools that mutate external state
