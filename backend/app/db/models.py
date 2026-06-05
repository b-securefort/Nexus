"""
SQLModel table definitions per §7.1 of the PRD.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRecord(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    oid: str = Field(unique=True, nullable=False, index=True)
    email: str = Field(nullable=False)
    display_name: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_seen_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_oid: str = Field(nullable=False, index=True)
    title: str = Field(nullable=False)
    skill_id: str = Field(nullable=False)
    skill_snapshot_json: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    deleted_at: Optional[datetime] = Field(default=None)
    # Cached summary of older history past summary_through_message_id, set by
    # the compaction module when the in-prompt history exceeds thresholds.
    summary_text: Optional[str] = Field(default=None)
    summary_through_message_id: Optional[int] = Field(default=None)
    # A4 — Lease heartbeat for approval / long-turn recovery. The orchestrator
    # writes `lease_heartbeat_at` periodically while a chat turn is in flight;
    # `lease_owner` identifies the FastAPI worker that holds it (host pid for
    # single-host, hostname:pid in multi-replica deployments).
    #
    # The frontend polls these via GET /api/conversations/{id}/lease. If the
    # heartbeat is older than the staleness threshold (2× the heartbeat
    # interval), the worker that started the turn has likely died and the
    # UI surfaces a "Restart turn" affordance. We do NOT auto-reconstruct
    # synthetic retry / drawio iteration state — recovery means starting the
    # last user message as a fresh turn.
    lease_heartbeat_at: Optional[datetime] = Field(default=None)
    lease_owner: Optional[str] = Field(default=None)


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(nullable=False, index=True)
    role: str = Field(nullable=False)  # "user" | "assistant" | "tool"
    content: str = Field(nullable=False)
    tool_calls_json: Optional[str] = Field(default=None)
    tool_call_id: Optional[str] = Field(default=None)
    tool_name: Optional[str] = Field(default=None)
    attachments_json: Optional[str] = Field(default=None)  # JSON array of {filename, content_type, url}
    # Cached high-quality summary of a long user paste, populated lazily by
    # compaction when the original content exceeds USER_PASTE_THRESHOLD and
    # the message isn't the latest user turn. Persisted so the LLM cost is
    # paid once across the lifetime of the conversation.
    text_summary: Optional[str] = Field(default=None)
    # Cached vision-LLM description of attached images, populated lazily for
    # user messages with attachments that aren't the most recent image-bearing
    # turn. Lets the model "remember" earlier images without paying for the
    # full multipart payload each turn.
    image_summary: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class PersonalSkill(SQLModel, table=True):
    __tablename__ = "personal_skills"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_oid: str = Field(nullable=False, index=True)
    name: str = Field(nullable=False)
    display_name: str = Field(nullable=False)
    description: str = Field(default="", nullable=False)
    system_prompt: str = Field(nullable=False)
    tools_json: str = Field(default="[]", nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    deleted_at: Optional[datetime] = Field(default=None)


class PendingApproval(SQLModel, table=True):
    __tablename__ = "pending_approvals"

    id: str = Field(primary_key=True)  # UUID
    conversation_id: int = Field(nullable=False, index=True)
    user_oid: str = Field(nullable=False)
    tool_name: str = Field(nullable=False)
    tool_args_json: str = Field(nullable=False)
    reason: str = Field(nullable=False)  # generator's stated intent — audit only; the card shows risk_description
    status: str = Field(nullable=False, default="pending")  # pending | approved | denied | expired
    # Advisory risk verdict from the separate review LLM (see §5 2026-06-04).
    # risk_level: pending | safe | caution | destructive. None until the review
    # resolves; never gates execution on its own.
    risk_level: Optional[str] = Field(default=None)
    risk_description: Optional[str] = Field(default=None)  # neutral "what this command does"
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    resolved_at: Optional[datetime] = Field(default=None)
    result_json: Optional[str] = Field(default=None)


class KBChunk(SQLModel, table=True):
    """A chunk of a KB markdown file, used by the local hybrid retrieval path.

    The companion FTS5 (`kb_chunks_fts`) and sqlite-vec (`kb_chunks_vec`)
    virtual tables are managed in raw DDL — SQLModel only owns this regular
    content table. The FTS table is kept in sync by triggers; the vec0 table
    is written explicitly from the reindexer because embeddings aren't
    SQL-derivable.
    """
    __tablename__ = "kb_chunks"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Optional[int] = Field(default=None, primary_key=True)
    kb_path: str = Field(nullable=False, index=True)
    chunk_idx: int = Field(nullable=False)
    heading: str = Field(nullable=False)  # "Doc Title > Section > Subsection"
    text: str = Field(nullable=False)
    content_hash: str = Field(nullable=False)  # sha256 of source file content
    file_mtime: float = Field(nullable=False)
    source_url: Optional[str] = Field(default=None)  # from ingested front-matter
    # Disambiguates one configured wiki from another when an ingestion-source-type
    # (e.g. ado_wiki) has multiple instances. Required-by-convention for
    # source='ado_wiki' rows; NULL on hand-authored / PDF content.
    source_instance: Optional[str] = Field(default=None, index=True)
    embed_model: str = Field(nullable=False)  # e.g. "bge-small-en-v1.5"
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class AgentLearning(SQLModel, table=True):
    """Validated learning the agent has acquired from a verified success-after-failure event.

    Distinct from team-authored KB documents (which live in `kb_chunks`).
    This table is the agent's procedural + semantic memory layer:
      - `type='semantic'`  — toolchain facts ("Resource Graph KQL uses `=~` for case-insensitive")
      - `type='procedural'` — how to do things ("for AKS upgrades, prefer `az aks nodepool upgrade` over `az aks upgrade --node-image-only`")

    Writes are gated to the orchestrator's success-after-failure detector
    (see `app/agent/learnings.py::record_validated_learning`). The agent
    itself cannot write via a tool call — that path is removed.

    Companion virtual table `agent_learnings_vec` (vec0) stores embeddings
    for retrieval; populated by `app/agent/learnings.py::reembed_dirty()`.
    """
    __tablename__ = "agent_learnings"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(nullable=False, index=True)  # "semantic" | "procedural"
    category: str = Field(nullable=False)  # "syntax-fix" | "known-issue" | "workaround" | "best-practice" | "gotcha"
    tool_name: str = Field(nullable=False, index=True)
    summary: str = Field(nullable=False)
    details: str = Field(nullable=False)
    # Provenance of the learning. Gates which lifecycle rules apply, not just audit:
    #   "failure_success" — derived from a tracked tool failure→success transition
    #                       (reality-grounded; validated by tool outcome).
    #   "user_correction" — extracted from an explicit user teach-intent turn
    #                       (assertion-grounded; never auto-promoted by tool
    #                       outcome, archived by a later contradicting correction).
    # See DESIGN.md §5 2026-06-05 "User-correction learning capture".
    source: str = Field(nullable=False, default="failure_success", index=True)
    status: str = Field(nullable=False, default="active", index=True)
    # status values:
    #   "active"      — retrievable, validated
    #   "provisional" — retrievable, awaiting more validations or migrated from legacy learn.md
    #   "archived"    — not retrievable, kept for audit (stale or superseded)
    #   "rejected"    — not retrievable, the LLM judge classified the write as hint-suppression
    originating_conversation_id: Optional[int] = Field(default=None, index=True)
    judge_verdict_json: Optional[str] = Field(default=None)  # full judge response for audit
    embed_model: Optional[str] = Field(default=None)
    content_hash: Optional[str] = Field(default=None)  # sha256 of summary+details — for reembed detection
    validation_count: int = Field(default=0, nullable=False)
    failure_count: int = Field(default=0, nullable=False)
    recorded_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_validated_at: Optional[datetime] = Field(default=None)
    last_retrieved_at: Optional[datetime] = Field(default=None)
    archived_at: Optional[datetime] = Field(default=None)


class PendingQuestion(SQLModel, table=True):
    """Persistent record of an `ask_user` tool call awaiting the user's answer.

    Mirrors the approval state machine: created when the tool fires, resolved
    when the user posts answers via the API, expired by the periodic sweeper
    when the timeout elapses.
    """
    __tablename__ = "pending_questions"

    id: str = Field(primary_key=True)  # UUID
    conversation_id: int = Field(nullable=False, index=True)
    user_oid: str = Field(nullable=False)
    # JSON list of {question, header, options:[{label, description}], multi_select}
    questions_json: str = Field(nullable=False)
    status: str = Field(nullable=False, default="pending")  # pending | answered | expired
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    resolved_at: Optional[datetime] = Field(default=None)
    # JSON list of {question, selected:[label, ...], notes?:str} once answered
    answers_json: Optional[str] = Field(default=None)
