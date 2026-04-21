"""Nexus Terminal Client — API communication layer."""

import json
import threading
from typing import Callable

import httpx

DEFAULT_BASE_URL = "http://localhost:8002"
DEFAULT_HEADERS = {"X-Dev-User": "dev-user"}


class NexusAPI:
    """Thin wrapper around the Nexus backend REST + SSE API."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}
        self._client = httpx.Client(base_url=self.base_url, headers=self.headers, timeout=10)

    # ── Skills ────────────────────────────────────────────────────────────

    def list_skills(self) -> list[dict]:
        r = self._client.get("/api/skills")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("value", data)

    def list_tools(self) -> list[dict]:
        r = self._client.get("/api/tools")
        r.raise_for_status()
        return r.json()

    # ── Conversations ─────────────────────────────────────────────────────

    def list_conversations(self) -> list[dict]:
        r = self._client.get("/api/conversations")
        r.raise_for_status()
        return r.json()

    def get_conversation(self, cid: int) -> dict:
        r = self._client.get(f"/api/conversations/{cid}")
        r.raise_for_status()
        return r.json()

    def delete_conversation(self, cid: int) -> None:
        r = self._client.delete(f"/api/conversations/{cid}")
        r.raise_for_status()

    # ── Chat (SSE streaming) ─────────────────────────────────────────────

    def chat_stream(
        self,
        message: str,
        on_event: Callable[[str, dict], None],
        conversation_id: int | None = None,
        skill_id: str | None = None,
        on_approval_needed: Callable[[dict], str] | None = None,
    ) -> None:
        """Send a chat message and stream SSE events.

        If *on_approval_needed* is provided it will be called from the **main
        thread** whenever an ``approval_required`` event arrives.  It must
        return ``"approve"`` or ``"deny"``.  The approval is resolved via a
        REST POST while the SSE stream stays open, and the backend resumes
        automatically.

        Without *on_approval_needed* the approval event is forwarded via
        *on_event* as before (caller must handle it externally).
        """
        body: dict = {"message": message}
        if conversation_id:
            body["conversation_id"] = conversation_id
        if skill_id:
            body["skill_id"] = skill_id

        client = httpx.Client(
            base_url=self.base_url,
            headers={**self.headers, "Accept": "text/event-stream"},
            timeout=httpx.Timeout(connect=10, read=600, write=10, pool=10),
        )

        try:
            resp = client.send(
                client.build_request("POST", "/api/chat", json=body),
                stream=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _extract_error_detail(exc)
            client.close()
            raise RuntimeError(detail) from exc
        except Exception:
            client.close()
            raise

        if on_approval_needed is None:
            # Simple path — no inline approval handling
            try:
                self._parse_sse(resp, on_event)
            finally:
                resp.close()
                client.close()
            return

        # Threaded path: read SSE in background, handle approvals in main thread
        lock = threading.Lock()
        pending: list[dict] = []
        stream_done = threading.Event()

        def _reader():
            def _inner_on_event(etype: str, data: dict):
                if etype == "approval_required":
                    with lock:
                        pending.append(data)
                else:
                    on_event(etype, data)
            try:
                self._parse_sse(resp, _inner_on_event)
            finally:
                stream_done.set()

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        try:
            while not stream_done.is_set():
                stream_done.wait(timeout=0.3)

                with lock:
                    batch = list(pending)
                    pending.clear()

                for appr in batch:
                    # Fire the on_event so the display layer can show the prompt
                    on_event("approval_required", appr)

                    aid = appr.get("approval_id")
                    if aid:
                        action = on_approval_needed(appr)
                        try:
                            self.resolve_approval(aid, action)
                        except Exception as e:
                            on_event("error", {"message": f"Approval POST failed: {e}"})
        finally:
            reader.join(timeout=5)
            resp.close()
            client.close()

    def resume_stream(
        self,
        conversation_id: int,
        on_event: Callable[[str, dict], None],
    ) -> None:
        """Resume an SSE stream (e.g. after approval)."""
        with httpx.Client(
            base_url=self.base_url,
            headers={**self.headers, "Accept": "text/event-stream"},
            timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10),
        ) as client:
            with client.stream("GET", f"/api/chat/resume?conversation_id={conversation_id}") as resp:
                resp.raise_for_status()
                self._parse_sse(resp, on_event)

    # ── Approvals ─────────────────────────────────────────────────────────

    def resolve_approval(self, approval_id: str, action: str) -> dict:
        r = self._client.post(
            f"/api/approvals/{approval_id}",
            json={"action": action},
        )
        r.raise_for_status()
        return r.json()

    # ── SSE parser ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_sse(resp: httpx.Response, on_event: Callable[[str, dict], None]) -> None:
        event_type = ""
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event_type = line[7:].strip()
            elif line.startswith("data: "):
                data_str = line[6:].strip()
                try:
                    data = json.loads(data_str)
                    on_event(event_type, data)
                except json.JSONDecodeError:
                    pass
            # blank line resets event type
            elif not line.strip():
                event_type = ""

    def close(self):
        self._client.close()


def _extract_error_detail(exc: httpx.HTTPStatusError) -> str:
    """Pull the ``detail`` field from an HTTP error JSON body, or fall back to the status."""
    try:
        body = exc.response.json()
        if isinstance(body, dict) and "detail" in body:
            detail = body["detail"]
            if isinstance(detail, list):
                return "; ".join(d.get("msg", str(d)) for d in detail)
            return str(detail)
    except Exception:
        pass
    return f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
