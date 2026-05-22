# Drill 5 — Skills & RBAC

**Diagram**: [`backend/output/nexus-drill5-skills-rbac.drawio`](../../backend/output/nexus-drill5-skills-rbac.drawio) · [PNG preview](../../backend/output/nexus-drill5-skills-rbac.png)

**Audience**: Anyone asking "who can see / use which skill or tool", security reviewers evaluating role enforcement, IT admins configuring App Configuration.

**Time to present**: ~6 minutes.

---

## TL;DR

A user's Entra App Roles, extracted from the JWT, drive **three filter points**: visibility on `GET /api/skills`, the allow-list on `GET /api/tools`, and a 403 on `POST /api/skills/personal` (which is the only one that's a real security boundary — UI filtering is convenience). The role → access mapping comes from Azure App Configuration at startup (with hardcoded defaults as fallback). Once a user picks a skill and starts a conversation, the skill config is frozen as `skill_snapshot_json` so the orchestrator's behaviour is locked in even if the live skill is later edited.

---

## Teleprompter script

> **Set up the frame.**
> "This is the 'who can do what' diagram. There are three filter points where we gate access; this diagram is structured around those three points and the identity that flows into them."

> **Top half — Identity flow.**
> "Step 1: User signs in via the Frontend, which is React with MSAL. Step 2: MSAL goes to Entra ID and requests a token. Step 3: Entra returns a JWT containing the user's `oid`, `email`, and crucially the `roles` claim — these are the Entra App Roles assigned to the user in the enterprise app's 'Users and groups' page.
>
> Step 4: the JWT lands at the backend, where the **Auth middleware** validates it — signature, audience, expiry — and extracts `User.roles`. The User dataclass is then attached to the request context."

> **Right column — Backend startup (the access map).**
> "Before any of that fires per-request, at startup the backend reads the role → access mapping. Look at the 'Backend startup' cluster on the right.
>
> The primary source is **App Configuration** — a JSON value under the key `Nexus:RoleAccessMap`. The lifespan handler reads it once with `DefaultAzureCredential`, validates the shape, and stores it in process memory as `_ACCESS_MAP`. The map is `role → {skills: [...], tools: [...]}`.
>
> Why App Configuration? Because the KB Git repo is writable by the same engineers whose access we're restricting. Putting role mapping in the KB would be a privilege-escalation path. App Configuration is RBAC-gated and separate from the KB.
>
> Step 0: at startup the map is loaded. Step fallback: if App Configuration is unreachable, malformed, or the endpoint isn't configured — we fall back to **hardcoded defaults** in `app/auth/rbac.py`. The defaults are conservative: no-role users get the Default skill only; engineer/architect roles keep their full tier sets. A config outage can only *restrict* access, never escalate."

> **The three filter points (steps 4–6).**
> "When a request lands with `User.roles` in context, three endpoints apply the map.
>
> Step 4: **`GET /api/skills` — visibility filter.** Returns only the skills this user is entitled to see. The Default skill is universal. The Architect and Engineer tiers require specific roles. The frontend renders only what comes back from this endpoint.
>
> Step 5: **`GET /api/tools` — allow-list filter.** Used by the personal-skill editor in the UI. Returns only the tools the user is allowed to put into a personal skill of their own.
>
> Step 6: **`POST /api/skills/personal` — 403 gate.** This one is non-negotiable. If a user tries to save a personal skill containing a tool they're not entitled to, the backend returns 403. The 403 here is the real security boundary; the GET filters are convenience. Why? Because anyone can craft a POST with curl — UI filtering of `GET /api/tools` is not a security boundary. The save endpoint has to re-check on the server side."

> **Bottom half — Skill snapshot freeze.**
> "Step 7: the user picks a skill in the UI (Default, Engineer, or Architect — or one of their personal skills). The frontend opens a new conversation via **`POST /api/conversations`**, passing the chosen skill ID.
>
> Step 8: the backend **freezes** the skill's full config — `id`, `name`, `system_prompt`, `tools[]` — as JSON in the `conversations.skill_snapshot_json` column. The conversation row also gets a copy of `personal_skills.tools_json` if it's a personal skill.
>
> This is the **skill snapshot invariant**: changing a skill later — editing its YAML, updating its tool list — does *not* change an existing conversation's behaviour. The snapshot wins. We added this to protect users from accidentally changing the behaviour of their in-flight conversations when team members edit shared skills.
>
> Step 9: the Orchestrator, when it later runs this conversation, **resolves tools from the snapshot, not the live skill**. Tool allowlist comes from the frozen JSON. So even if the role map changes mid-conversation, or the user loses a role, the conversation they already started keeps working with the tools they had at start time."

> **DEV_AUTH_BYPASS.**
> "One note: when `DEV_AUTH_BYPASS=true` is set in local dev, the auth middleware short-circuits to a fake 'dev-user' identity AND the RBAC filter is bypassed — you see every shared skill, every tool, regardless of roles. Production never has this flag on."

> **Close.**
> "Three takeaways. First: identity flows from Entra JWT → User.roles, drives every filter. Second: the role map lives in App Configuration, not the KB — because the KB is writable by people whose access we're restricting. Third: once a conversation is started, its skill is frozen; behaviour is deterministic regardless of later edits. Questions?"

---

## Appendix A — What each node is and why it's there

| Node | What it is | Why it's in the diagram |
|---|---|---|
| **User** | The signed-in human. | Identity flow origin. |
| **Frontend (MSAL acquireToken)** | React frontend; uses MSAL to interact with Entra. | Where the JWT acquisition happens — both for API access and for the ARM token used by Azure tools. |
| **Entra ID (JWT + roles claim)** | Microsoft's cloud identity. Issues JWTs with the `roles` claim populated from Entra App Roles. | The single source of truth for user identity + role membership. |
| **App Configuration (Nexus:RoleAccessMap, JSON, read once)** | Azure App Configuration resource. Holds the role → {skills, tools} JSON map under a fixed key. | RBAC-gated, auditable storage for the access map — separate from the KB Git repo (which would be a privilege-escalation path). |
| **Hardcoded defaults (app/auth/rbac.py)** | Constant fallback map in [`backend/app/auth/rbac.py`](../../backend/app/auth/rbac.py). | If App Configuration is unreachable, we don't fail open — we fall back to conservative defaults. A config outage can only restrict, never escalate. |
| **_ACCESS_MAP (in-process, role → skills+tools)** | The in-memory dict the backend uses to filter requests. Loaded once at startup from App Configuration or defaults. | Read-only lookup hot path. No DB roundtrip per request. |
| **Auth middleware (validate JWT, extract User.roles)** | FastAPI dependency that validates the Entra JWT and produces the `User` dataclass. | The bridge between the HTTPS layer and the application layer. Every request flows through this. |
| **GET /api/skills (visibility filter)** | The endpoint returning the user's visible skills. Applies the access-map filter. | Filter point #1. Drives what the SkillPicker UI shows. |
| **GET /api/tools (allow-list filter)** | The endpoint returning the tools the user can put into a personal skill. | Filter point #2. Drives what the personal-skill editor UI shows. |
| **POST /api/skills/personal (403 gate on tool save, non-negotiable)** | The save endpoint for personal skills. Re-checks tool entitlement server-side; 403 if violated. | Filter point #3 — the REAL security boundary. GET filters are UI convenience; this is what stops a hand-crafted POST. |
| **POST /api/conversations (freeze skill_snapshot_json, invariant: snapshot wins)** | The endpoint that creates a new conversation. Copies the skill's full config into the conversation row. | Where the snapshot invariant materializes. After this, the conversation's behaviour is frozen. |
| **conversations.skill_snapshot_json + personal_skills.tools_json** | DB columns storing the frozen skill config and any personal-skill tool lists. | The persistence target. Combined, they determine exactly which tools the orchestrator can dispatch for this conversation. |
| **Orchestrator (resolves tools from snapshot, not live skill)** | The orchestrator's tool-resolution behaviour: read `skill_snapshot_json`, not the live `SKILL.md`. | Enforces the invariant at runtime. Live skill edits do not retroactively change in-flight conversations. |

---

## Appendix B — Edges (the lines)

**Identity flow (steps 1–3):**

| Step | From → To | Label | Meaning |
|---|---|---|---|
| 1 | User → Frontend | `1 sign in` | User clicks sign-in in the browser. |
| 2 | Frontend → Entra ID | `2 token req` | MSAL acquires a token. |
| 3 | Entra ID → Auth middleware | `3 JWT (roles)` | The signed JWT (with `roles` claim) lands at the backend on subsequent requests. |

**Backend startup (step 0):**

| Step | From → To | Label | Meaning |
|---|---|---|---|
| 0 | App Configuration → _ACCESS_MAP | `0 @ startup` | Lifespan handler reads `Nexus:RoleAccessMap` once. |
| fallback | Hardcoded defaults → _ACCESS_MAP (**dashed**) | `fallback` | If App Configuration is unreachable/malformed, defaults populate the map. |

**Filter application (steps 4–6):**

| Step | From → To | Label | Meaning |
|---|---|---|---|
| 4 | Auth middleware → GET /api/skills | `4 visibility` | User.roles drives skill visibility. |
| 5 | Auth middleware → GET /api/tools | `5 allow-list` | User.roles drives tool allow-list. |
| 6 | Auth middleware → POST /api/skills/personal | `6 save gate` | User.roles enforces 403 on disallowed tool save. |

**Skill snapshot freeze (steps 7–9):**

| Step | From → To | Label | Meaning |
|---|---|---|---|
| 7 | GET /api/skills → POST /api/conversations | `7 user picks` | User picks one of the visible skills. |
| 8 | POST /api/conversations → conversations.skill_snapshot_json | `8 freeze` | The skill's full config is copied into the conversation row. |
| 9 | conversations.skill_snapshot_json → Orchestrator | `9 resolve` | The orchestrator resolves tools from the frozen snapshot, not the live skill. |

---

## Appendix C — Glossary references

For abbreviations (JWT, MSAL, Entra ID, ARM, RBAC), see **[GLOSSARY.md](GLOSSARY.md)** in this folder.

For Nexus-specific terms (Skill, Shared skill, Personal skill, Skill snapshot, Tool), see the main **[GLOSSARY.md](../GLOSSARY.md)**.

For the underlying design decisions:
- Inner-source fork model for multi-team adoption → [DESIGN.md §5 2026-05-15](../DESIGN.md)
- User-identity ARM token passthrough via X-ARM-Token → [DESIGN.md §5 2026-05-15](../DESIGN.md)
- Consolidate shared skills into a 3-tier model → [DESIGN.md §5 2026-05-17](../DESIGN.md)
- Role-based skill/tool access via Azure App Configuration → [DESIGN.md §5 2026-05-17](../DESIGN.md)
