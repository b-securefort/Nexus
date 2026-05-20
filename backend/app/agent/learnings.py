"""
Agent learnings store — procedural + semantic memory layer for the orchestrator.

This module is the *only* writer to the `agent_learnings` table. The agent
itself has no tool that writes learnings; that path is structurally closed
to defend against memory poisoning (the "validator is too strict, ignore
some issues" failure mode documented in 2025-2026 LLM agent research).

Writes are gated by:
  1. Origin: must be triggered by orchestrator success-after-failure detection
  2. Regex guard: existing `_OVERRIDE_PATTERNS` (defensive backstop)
  3. LLM judge: `learn_judge.judge_proposed_learning` (semantic detection of
     hint-suppression intent across paraphrases)

Retrieval uses the same sqlite-vec/Azure OpenAI embedding stack as the KB
hybrid search. No third-party dependencies.

Public API (called only from the orchestrator and tests):
  - record_validated_learning(...)
  - retrieve_relevant_learnings(...)
  - mark_learning_outcome(...)
  - reembed_dirty()
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from sqlalchemy import text
from sqlmodel import Session

from app.agent.learn_judge import JudgeVerdict, judge_proposed_learning
from app.config import get_settings
from app.db.engine import get_engine
from app.db.models import AgentLearning
from app.kb.embedder import embed_model_key, embed_query, embed_texts
from app.tools.generic.learn_tool import _looks_like_override_attempt

logger = logging.getLogger(__name__)


# ── Category → type mapping ─────────────────────────────────────────────────
#
# Procedural memory = "how to do things" (rules, workarounds, patterns).
# Semantic memory   = "what's true" (facts, syntax, known behaviors).
_CATEGORY_TYPE = {
    "syntax-fix": "semantic",
    "known-issue": "semantic",
    "gotcha": "semantic",
    "workaround": "procedural",
    "best-practice": "procedural",
}
VALID_CATEGORIES = set(_CATEGORY_TYPE.keys())


# ── Environment-specific text detector (write-time sanitization) ────────────
#
# Block entries that name specific resources (won't generalize). This is
# orthogonal to the override-pattern guard — that one blocks hint-suppression
# intent; this one blocks resource-naming leakage.
_GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# common Azure resource name patterns: <prefix>-<env>-<region>-<num> with realistic prefixes
_AZURE_NAME_RE = re.compile(
    r"\b(rg|vm|st|kv|appgw|aks|cosmosdb|sql|sb|eh|func|app|dns|nsg|vnet|snet|pip|lb|fw|le)"
    r"[-_]?[a-z0-9]+[-_](dev|test|qa|stage|stg|uat|prod|prd)\b",
    re.IGNORECASE,
)


def _looks_environment_specific(text_: str) -> tuple[bool, str]:
    """Return (is_specific, reason) if the text references concrete resources."""
    if _GUID_RE.search(text_):
        return True, "contains a GUID/UUID (likely a subscription or resource ID)"
    m = _AZURE_NAME_RE.search(text_)
    if m:
        return True, f"contains an Azure-style resource name ({m.group(0)!r})"
    return False, ""


# ── Retrieval result ────────────────────────────────────────────────────────

@dataclass
class RetrievedLearning:
    id: int
    type: str
    category: str
    tool_name: str
    summary: str
    details: str
    status: str
    validation_count: int
    failure_count: int
    score: float  # higher = more relevant


# ── vec0 serialisation (mirrors kb/vector_store.py) ─────────────────────────

def _serialise_vec(arr: np.ndarray) -> bytes:
    return struct.pack(f"{len(arr)}f", *arr.astype(np.float32).tolist())


def _content_hash(summary: str, details: str) -> str:
    return hashlib.sha256(f"{summary}\n\n{details}".encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Write path: orchestrator-only ───────────────────────────────────────────

def record_validated_learning(
    *,
    session: Session,
    tool_name: str,
    category: str,
    summary: str,
    details: str,
    prior_failures_summary: str,
    originating_conversation_id: Optional[int] = None,
    skip_judge: bool = False,
) -> Optional[AgentLearning]:
    """Record a learning, gated by three defenses in this order:
      1. Category whitelist + summary/details non-empty
      2. Regex override-pattern guard (cheap, deterministic)
      3. Environment-specific name guard (no resource IDs)
      4. LLM judge (semantic hint-suppression detection)

    Returns the persisted `AgentLearning` on success, or None if rejected.
    The orchestrator is the only legitimate caller. `skip_judge=True` is for
    the legacy-import migration path where the judge has already been run
    (or where the LLM is unavailable at startup); never use it from the
    request path.
    """
    if category not in VALID_CATEGORIES:
        logger.warning("Rejecting learning: unknown category %r", category)
        return None
    if not summary.strip() or not details.strip():
        logger.warning("Rejecting learning: empty summary or details")
        return None

    # Defense 1 — regex override guard (already in production)
    if _looks_like_override_attempt(summary, details):
        logger.warning(
            "Rejecting learning by regex guard: tool=%s summary=%r",
            tool_name, summary[:120],
        )
        return None

    # Defense 2 — environment-specific naming
    is_specific, why = _looks_environment_specific(f"{summary}\n{details}")
    if is_specific:
        logger.warning(
            "Rejecting learning by name guard (%s): tool=%s summary=%r",
            why, tool_name, summary[:120],
        )
        return None

    # Defense 3 — LLM judge
    verdict: Optional[JudgeVerdict] = None
    if not skip_judge:
        verdict = judge_proposed_learning(
            summary=summary,
            details=details,
            tool_name=tool_name,
            prior_failures_summary=prior_failures_summary,
        )
        if not verdict.approve:
            logger.warning(
                "Rejecting learning by LLM judge: tool=%s suppression=%s reason=%r",
                tool_name, verdict.is_suppression_attempt, verdict.reason,
            )
            # Persist a "rejected" record for audit so we can spot poisoning trends.
            try:
                rejected = AgentLearning(
                    type=_CATEGORY_TYPE[category],
                    category=category,
                    tool_name=tool_name or "general",
                    summary=summary,
                    details=details,
                    status="rejected",
                    originating_conversation_id=originating_conversation_id,
                    judge_verdict_json=json.dumps({
                        "approve": verdict.approve,
                        "is_suppression_attempt": verdict.is_suppression_attempt,
                        "confidence": verdict.confidence,
                        "reason": verdict.reason,
                    }),
                    content_hash=_content_hash(summary, details),
                    recorded_at=_utcnow(),
                )
                session.add(rejected)
                session.commit()
            except Exception:
                logger.exception("Failed to persist rejected-learning audit row")
            return None

    # All defenses passed — persist
    learning = AgentLearning(
        type=_CATEGORY_TYPE[category],
        category=category,
        tool_name=tool_name or "general",
        summary=summary,
        details=details,
        status="provisional",  # promoted to "active" after re-validation
        originating_conversation_id=originating_conversation_id,
        judge_verdict_json=(
            json.dumps({
                "approve": verdict.approve,
                "is_suppression_attempt": verdict.is_suppression_attempt,
                "confidence": verdict.confidence,
                "reason": verdict.reason,
            }) if verdict else None
        ),
        content_hash=_content_hash(summary, details),
        embed_model=None,  # populated by reembed_dirty()
        recorded_at=_utcnow(),
    )
    session.add(learning)
    session.commit()
    session.refresh(learning)
    logger.info(
        "Recorded learning id=%s type=%s tool=%s category=%s",
        learning.id, learning.type, learning.tool_name, learning.category,
    )

    # Embed inline if possible — the conversation that triggered this write
    # likely benefits from immediate availability of the new learning.
    try:
        reembed_dirty(limit=1)
    except Exception:
        logger.exception("Inline reembed failed (will be picked up by background sweep)")

    return learning


def derive_learning_from_success(
    *,
    tool_name: str,
    final_successful_args: dict,
    prior_failures: list[tuple[dict, str]],
) -> dict:
    """Build a structured summary + details + category from the orchestrator's
    tracked failure → success transition.

    Returns kwargs compatible with `record_validated_learning`. This is the
    *content derivation* step — judge + storage gates are still applied
    downstream by `record_validated_learning`.
    """
    # Pick the most recent failure as the canonical "what was wrong"
    last_failure_args, last_failure_msg = prior_failures[-1] if prior_failures else ({}, "")
    fail_arg_repr = json.dumps(last_failure_args, ensure_ascii=False)[:300]
    success_arg_repr = json.dumps(final_successful_args, ensure_ascii=False)[:300]
    fail_msg_short = (last_failure_msg or "")[:400].replace("\n", " ").strip()

    # Heuristic categorisation: syntax errors → syntax-fix; auth/permission →
    # known-issue; otherwise workaround.
    lowered = fail_msg_short.lower()
    if any(t in lowered for t in (
        "syntaxerror", "invalid syntax", "unexpected token", "parse error",
        "invalid query", "expected", "unrecognized argument",
    )):
        category = "syntax-fix"
    elif any(t in lowered for t in (
        "unauthorized", "forbidden", "access denied", "permission",
        "authenticationfailed", "authorization", "rbac",
    )):
        category = "known-issue"
    else:
        category = "workaround"

    summary = (
        f"For `{tool_name}`, the failure pattern '{fail_arg_repr[:80]}' "
        f"is resolved by switching to '{success_arg_repr[:80]}'"
    )
    details = (
        f"Tool: {tool_name}\n"
        f"Last failing args: {fail_arg_repr}\n"
        f"Failure message: {fail_msg_short}\n"
        f"Working args: {success_arg_repr}\n"
        f"Failure count before success: {len(prior_failures)}"
    )
    return {
        "tool_name": tool_name,
        "category": category,
        "summary": summary,
        "details": details,
        "prior_failures_summary": _format_prior_failures(prior_failures),
    }


def _format_prior_failures(prior_failures: list[tuple[dict, str]]) -> str:
    """Compact representation for the judge's context."""
    if not prior_failures:
        return "(no prior failures recorded)"
    lines = []
    for i, (args, err) in enumerate(prior_failures, 1):
        arg_str = json.dumps(args, ensure_ascii=False)[:160]
        err_str = (err or "")[:200].replace("\n", " ").strip()
        lines.append(f"{i}. args={arg_str}  error={err_str}")
    return "\n".join(lines)


# ── Read path: retrieval ────────────────────────────────────────────────────

def retrieve_relevant_learnings(
    *,
    query: str,
    tool_name_hint: Optional[str] = None,
    top_k: int = 5,
) -> list[RetrievedLearning]:
    """Return up to `top_k` relevant learnings for the current context.

    `query` should be a short natural-language description of what the agent
    is about to do — typically derived from the user's first message + the
    current skill name. `tool_name_hint` gives a soft preference for entries
    tagged with that tool but does not exclude others.

    Currently a pure embedding-similarity search. BM25 could be added later
    for a hybrid retrieval if the volume grows large enough to warrant it.
    """
    if top_k <= 0:
        return []
    try:
        qvec = embed_query(query)
    except Exception as e:
        logger.warning("Learnings retrieval skipped — embed_query failed: %s", e)
        return []

    engine = get_engine()
    with engine.connect() as conn:
        try:
            # Pull a generous candidate pool from vec0 — we'll re-rank below.
            rows = conn.execute(
                text(
                    "SELECT rowid, distance FROM agent_learnings_vec "
                    "WHERE embedding MATCH :v ORDER BY distance LIMIT :k"
                ),
                {"v": _serialise_vec(qvec), "k": max(top_k * 4, 20)},
            ).fetchall()
        except Exception as e:
            logger.warning("agent_learnings_vec search failed: %s", e)
            return []

        if not rows:
            return []

        rowid_to_dist = {r[0]: r[1] for r in rows}
        placeholders = ",".join(str(r[0]) for r in rows)
        meta_rows = conn.execute(
            text(
                f"SELECT id, type, category, tool_name, summary, details, status, "
                f"validation_count, failure_count "
                f"FROM agent_learnings WHERE id IN ({placeholders}) "
                f"AND status IN ('active', 'provisional')"
            )
        ).fetchall()

    results: list[RetrievedLearning] = []
    for r in meta_rows:
        lid, ltype, lcat, ltool, lsum, ldet, lstat, vcount, fcount = r
        # Base score from inverse distance (closer = higher score)
        dist = float(rowid_to_dist.get(lid, 1.0))
        score = 1.0 / (dist + 0.01)
        # Status boost: active > provisional
        if lstat == "active":
            score *= 1.5
        # Tool-name match boost
        if tool_name_hint and ltool == tool_name_hint:
            score *= 1.3
        # Validation track record
        if vcount > 0:
            score *= 1.0 + min(0.3, vcount * 0.05)
        if fcount > 0:
            score *= max(0.5, 1.0 - fcount * 0.1)
        results.append(RetrievedLearning(
            id=lid, type=ltype, category=lcat, tool_name=ltool,
            summary=lsum, details=ldet, status=lstat,
            validation_count=vcount, failure_count=fcount,
            score=score,
        ))

    results.sort(key=lambda x: x.score, reverse=True)
    selected = results[:top_k]

    # Mark retrieved entries' last_retrieved_at for the validation-on-outcome path
    if selected:
        ids = [s.id for s in selected]
        with engine.connect() as conn:
            conn.execute(
                text(
                    f"UPDATE agent_learnings SET last_retrieved_at = :ts "
                    f"WHERE id IN ({','.join(str(i) for i in ids)})"
                ),
                {"ts": _utcnow().isoformat()},
            )
            conn.commit()

    return selected


# ── Validation tracking ─────────────────────────────────────────────────────

# Threshold for auto-promoting provisional → active
PROMOTION_VALIDATION_THRESHOLD = 3
# Threshold for auto-archiving on consistent failure
ARCHIVE_FAILURE_THRESHOLD = 3


def mark_learning_outcome(learning_ids: list[int], succeeded: bool) -> None:
    """Update validation_count or failure_count for the given learnings.

    Called by the orchestrator after a tool call resolves — if the agent had
    these learnings in context AND the operation succeeded, count this as a
    validation. If it failed, count as a failure. Drift-handling: too many
    failures auto-archive the entry; enough validations auto-promote.

    Heuristic, not precise — the agent may have ignored the retrieved
    learning. But across many turns it provides a directional signal that
    distinguishes load-bearing entries from drifted ones.
    """
    if not learning_ids:
        return
    placeholders = ",".join(str(i) for i in learning_ids)
    engine = get_engine()
    now = _utcnow().isoformat()
    with engine.connect() as conn:
        if succeeded:
            conn.execute(
                text(
                    f"UPDATE agent_learnings "
                    f"SET validation_count = validation_count + 1, "
                    f"    last_validated_at = :ts "
                    f"WHERE id IN ({placeholders})"
                ),
                {"ts": now},
            )
            # Auto-promote provisional → active when threshold reached
            conn.execute(
                text(
                    f"UPDATE agent_learnings SET status = 'active' "
                    f"WHERE id IN ({placeholders}) "
                    f"  AND status = 'provisional' "
                    f"  AND validation_count >= :thr"
                ),
                {"thr": PROMOTION_VALIDATION_THRESHOLD},
            )
        else:
            conn.execute(
                text(
                    f"UPDATE agent_learnings "
                    f"SET failure_count = failure_count + 1 "
                    f"WHERE id IN ({placeholders})"
                ),
            )
            # Auto-archive when failures dominate
            conn.execute(
                text(
                    f"UPDATE agent_learnings "
                    f"SET status = 'archived', archived_at = :ts "
                    f"WHERE id IN ({placeholders}) "
                    f"  AND failure_count >= :thr "
                    f"  AND failure_count > validation_count"
                ),
                {"ts": now, "thr": ARCHIVE_FAILURE_THRESHOLD},
            )
        conn.commit()


# ── Embedding population (called after writes, and as a background sweep) ───

def reembed_dirty(limit: int = 50) -> int:
    """Embed any AgentLearning rows with NULL embed_model (or stale model key).

    Returns count embedded. Cheap when nothing is dirty. Designed to be
    called inline after a write (limit=1) and as a periodic background
    sweep (default limit=50).
    """
    settings = get_settings()
    current_model = embed_model_key()
    engine = get_engine()
    embedded = 0

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, summary, details FROM agent_learnings "
                "WHERE status IN ('active', 'provisional') "
                "  AND (embed_model IS NULL OR embed_model <> :m) "
                "ORDER BY id LIMIT :lim"
            ),
            {"m": current_model, "lim": limit},
        ).fetchall()

        if not rows:
            return 0

        ids = [r[0] for r in rows]
        texts_to_embed = [f"{r[1]}\n\n{r[2]}" for r in rows]
        try:
            vectors = embed_texts(texts_to_embed)
        except Exception as e:
            logger.warning("reembed_dirty: embed_texts failed: %s", e)
            return 0

        for lid, vec in zip(ids, vectors):
            try:
                # vec0 has no UPSERT; delete-then-insert
                conn.execute(
                    text("DELETE FROM agent_learnings_vec WHERE rowid = :rid"),
                    {"rid": lid},
                )
                conn.execute(
                    text(
                        "INSERT INTO agent_learnings_vec (rowid, embedding) "
                        "VALUES (:rid, :emb)"
                    ),
                    {"rid": lid, "emb": _serialise_vec(vec)},
                )
                conn.execute(
                    text(
                        "UPDATE agent_learnings SET embed_model = :m WHERE id = :rid"
                    ),
                    {"m": current_model, "rid": lid},
                )
                embedded += 1
            except Exception:
                logger.exception("reembed_dirty: failed for learning id=%s", lid)
        conn.commit()

    if embedded:
        logger.info("Embedded %d agent learnings (model=%s)", embedded, current_model)
    return embedded


# ── One-time migration from legacy learn.md ─────────────────────────────────

def migrate_legacy_learn_md(session: Session) -> int:
    """Import existing kb_data/learnings/learn.md entries as provisional rows.

    Idempotent: skips entries whose content_hash already exists. Bypasses the
    LLM judge because (a) some legacy entries pre-date the judge and might
    not all pass, and (b) we mark them `provisional` so they go through the
    standard validation-or-archive lifecycle on use anyway. The regex guard
    still applies — known hint-suppression entries don't survive migration.
    """
    import os
    from app.tools.generic.learn_tool import _LEARN_FILE, _split_entries

    path = os.path.abspath(_LEARN_FILE)
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning("migrate_legacy_learn_md: read failed: %s", e)
        return 0

    _, entries = _split_entries(content)
    imported = 0
    for entry in entries:
        # Parse out category + summary + tool + details from the markdown shape
        m = re.match(r"^## \[([^\]]+)\]\s+(.+?)(?:\n|$)(.*)", entry, flags=re.DOTALL)
        if not m:
            continue
        category = m.group(1).strip().lower()
        summary = m.group(2).strip()
        body = m.group(3)
        if category not in VALID_CATEGORIES:
            continue

        tool_match = re.search(r"\*\*Tool\*\*:\s*(.+?)(?:\n|$)", body)
        tool_name = tool_match.group(1).strip() if tool_match else "general"
        details_match = re.search(r"\*\*Details\*\*:\s*(.+?)(?:\n\n|\Z)", body, flags=re.DOTALL)
        details = details_match.group(1).strip() if details_match else body.strip()

        # Skip if regex guard catches it
        if _looks_like_override_attempt(summary, details):
            logger.info("Skipping legacy entry by regex guard: %r", summary[:60])
            continue
        # Skip if name guard catches it
        if _looks_environment_specific(f"{summary}\n{details}")[0]:
            logger.info("Skipping legacy entry by name guard: %r", summary[:60])
            continue

        chash = _content_hash(summary, details)
        # Idempotency check
        existing = session.exec(
            text("SELECT id FROM agent_learnings WHERE content_hash = :h").bindparams(h=chash)
        ).first()
        if existing:
            continue

        row = AgentLearning(
            type=_CATEGORY_TYPE.get(category, "semantic"),
            category=category,
            tool_name=tool_name,
            summary=summary,
            details=details,
            status="provisional",
            content_hash=chash,
            recorded_at=_utcnow(),
        )
        session.add(row)
        imported += 1

    if imported:
        session.commit()
        logger.info("Migrated %d legacy learn.md entries to agent_learnings", imported)

    return imported
