import { apiFetch, apiFetchMultipart } from "./client";
import type { ChatRequest, ApprovalInfo } from "../types";

export async function sendChatMessage(
  body: ChatRequest,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  const hasFiles = body.files && body.files.length > 0;
  console.log(`[Nexus] sendChatMessage: hasFiles=${hasFiles}, fileCount=${body.files?.length ?? 0}`);

  let response: Response;
  if (hasFiles) {
    const formData = new FormData();
    formData.append("message", body.message);
    if (body.conversation_id != null) {
      formData.append("conversation_id", String(body.conversation_id));
    }
    if (body.skill_id) {
      formData.append("skill_id", body.skill_id);
    }
    for (const file of body.files!) {
      formData.append("files", file);
    }
    response = await apiFetchMultipart("/api/chat", {
      method: "POST",
      body: formData,
      signal,
    });
  } else {
    response = await apiFetch("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: body.conversation_id,
        skill_id: body.skill_id,
        message: body.message,
      }),
      signal,
    });
  }

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  let eventType = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const dataStr = line.slice(6).trim();
        try {
          const data = JSON.parse(dataStr);
          onEvent(eventType, data);
        } catch {
          // Ignore malformed data
        }
      } else if (!line.trim()) {
        eventType = "";
      }
    }
  }
}

export async function resumeChat(
  conversationId: number,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await apiFetch(
    `/api/chat/resume?conversation_id=${conversationId}`,
    { signal }
  );

  if (!response.ok) throw new Error("Failed to resume chat");

  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";
  let eventType = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        try {
          const data = JSON.parse(line.slice(6).trim());
          onEvent(eventType, data);
        } catch {
          // Ignore
        }
      } else if (!line.trim()) {
        eventType = "";
      }
    }
  }
}

export async function resolveApproval(
  approvalId: string,
  action: "approve" | "deny"
): Promise<void> {
  const response = await apiFetch(`/api/approvals/${approvalId}`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Failed" }));
    throw new Error(err.detail || "Failed to resolve approval");
  }
}

export async function fetchGreeting(): Promise<string> {
  const response = await apiFetch("/api/greeting");
  if (!response.ok) return "";
  const data = await response.json();
  return data.greeting || "";
}
