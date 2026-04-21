#!/usr/bin/env python3
"""
Nexus Terminal Client — a hacker-style TUI for the Nexus AI assistant.

Usage:
    python main.py [--url http://localhost:8002]
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

from api import NexusAPI
from display import (
    console,
    print_approval_prompt,
    print_assistant_end,
    print_assistant_start,
    print_assistant_token,
    print_banner,
    print_conversation_loaded,
    print_conversations,
    print_error,
    print_help,
    print_history_message,
    print_separator,
    print_skills,
    print_tool_call_start,
    print_tool_executing,
    print_tool_output_chunk,
    print_tool_result,
    print_user_message,
    COLORS,
)

# ── Prompt style ──────────────────────────────────────────────────────────

PT_STYLE = Style.from_dict({
    "prompt": "ansicyan bold",
    "": "ansiwhite",
})


def get_input(prompt_text: str = "nexus") -> str:
    """Get user input with styled prompt."""
    try:
        return pt_prompt(
            HTML(f"<prompt>{prompt_text}</prompt><b> ❯ </b>"),
            style=PT_STYLE,
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return "/quit"


def get_approval_input() -> str:
    """Get approval decision from user."""
    try:
        return pt_prompt(
            HTML("<b>[</b><ansiyellow>approve</ansiyellow><b>] y/n ❯ </b>"),
            style=PT_STYLE,
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "n"


# ── Main app ──────────────────────────────────────────────────────────────

class NexusTerminal:
    def __init__(self, base_url: str):
        self.api = NexusAPI(base_url=base_url)
        self.conversation_id: int | None = None
        self.skill_id: str | None = None
        self.skills: list[dict] = []
        self._streaming_text = False

    def run(self):
        print_banner()
        self._load_skills()

        if not self.skills:
            print_error("No skills available. Is the backend running?")
            return

        # Default to first skill
        self.skill_id = self.skills[0]["id"]
        console.print(f"  [{COLORS['dim']}]Active skill:[/{COLORS['dim']}] [{COLORS['accent']}]{self.skills[0]['display_name']}[/{COLORS['accent']}]")
        console.print(f"  [{COLORS['dim']}]Type /help for commands, or just start chatting.[/{COLORS['dim']}]")
        print_separator()

        while True:
            try:
                user_input = get_input(
                    f"nexus:{self.skill_id}" if self.skill_id else "nexus"
                )
            except KeyboardInterrupt:
                console.print()
                continue

            if not user_input:
                continue

            if user_input.startswith("/"):
                should_quit = self._handle_command(user_input)
                if should_quit:
                    break
                continue

            self._send_message(user_input)

    # ── Commands ──────────────────────────────────────────────────────

    def _handle_command(self, cmd: str) -> bool:
        """Handle slash commands. Returns True if should quit."""
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if command in ("/quit", "/exit", "/q"):
            console.print(f"\n  [{COLORS['dim']}]Goodbye.[/{COLORS['dim']}]")
            return True

        elif command == "/help":
            print_help()

        elif command == "/skills":
            self._load_skills()
            print_skills(self.skills)

        elif command == "/skill":
            self._switch_skill(arg)

        elif command == "/new":
            self.conversation_id = None
            console.print(f"  [{COLORS['accent']}]New conversation started.[/{COLORS['accent']}]")
            print_separator()

        elif command == "/history":
            self._show_history()

        elif command == "/load":
            self._load_conversation(arg)

        elif command == "/clear":
            console.clear()
            print_banner()

        else:
            console.print(f"  [{COLORS['dim']}]Unknown command: {command}. Type /help.[/{COLORS['dim']}]")

        return False

    def _load_skills(self):
        try:
            self.skills = self.api.list_skills()
        except Exception as e:
            print_error(f"Failed to load skills: {e}")
            self.skills = []

    def _switch_skill(self, arg: str):
        if not arg:
            console.print(f"  [{COLORS['dim']}]Usage: /skill <id or number>[/{COLORS['dim']}]")
            print_skills(self.skills)
            return

        # Try by number
        try:
            idx = int(arg) - 1
            if 0 <= idx < len(self.skills):
                self.skill_id = self.skills[idx]["id"]
                self.conversation_id = None  # new conversation with new skill
                console.print(f"  [{COLORS['accent']}]Switched to: {self.skills[idx]['display_name']}[/{COLORS['accent']}]")
                return
        except ValueError:
            pass

        # Try by ID
        match = next((s for s in self.skills if s["id"] == arg), None)
        if match:
            self.skill_id = match["id"]
            self.conversation_id = None
            console.print(f"  [{COLORS['accent']}]Switched to: {match['display_name']}[/{COLORS['accent']}]")
        else:
            print_error(f"Skill not found: {arg}")

    def _show_history(self):
        try:
            convos = self.api.list_conversations()
            print_conversations(convos)
        except Exception as e:
            print_error(f"Failed to load history: {e}")

    def _load_conversation(self, arg: str):
        if not arg:
            console.print(f"  [{COLORS['dim']}]Usage: /load <number>[/{COLORS['dim']}]")
            self._show_history()
            return

        try:
            convos = self.api.list_conversations()
            idx = int(arg) - 1
            if not (0 <= idx < len(convos)):
                print_error(f"Invalid conversation number. Range: 1-{len(convos)}")
                return

            cid = convos[idx]["id"]
            detail = self.api.get_conversation(cid)
            self.conversation_id = cid
            self.skill_id = detail.get("skill_id", self.skill_id)

            print_conversation_loaded(
                detail["title"],
                len(detail.get("messages", [])),
                detail.get("skill_id", "?"),
            )

            # Print last few messages
            msgs = detail.get("messages", [])
            recent = msgs[-10:] if len(msgs) > 10 else msgs
            if len(msgs) > 10:
                console.print(f"  [{COLORS['dim']}]... ({len(msgs) - 10} earlier messages)[/{COLORS['dim']}]")
            for m in recent:
                print_history_message(m["role"], m.get("content", ""), m.get("tool_calls_json"))

            print_separator()

        except ValueError:
            print_error("Invalid number. Usage: /load <number>")
        except Exception as e:
            print_error(f"Failed to load conversation: {e}")

    # ── Chat ──────────────────────────────────────────────────────────

    def _send_message(self, message: str):
        print_user_message(message)
        self._streaming_text = False

        def on_event(event_type: str, data: dict):
            if event_type == "message_saved":
                pass  # silently handled

            elif event_type == "token":
                if not self._streaming_text:
                    print_assistant_start()
                    self._streaming_text = True
                print_assistant_token(data.get("text", ""))

            elif event_type == "tool_call_start":
                if self._streaming_text:
                    print_assistant_end()
                    self._streaming_text = False
                print_tool_call_start(data.get("name", "?"), data.get("args", {}))

            elif event_type == "approval_required":
                if self._streaming_text:
                    print_assistant_end()
                    self._streaming_text = False
                # Display is handled here; decision is handled by on_approval_needed
                pass

            elif event_type == "tool_executing":
                print_tool_executing(data.get("name", "?"))

            elif event_type == "tool_output_chunk":
                print_tool_output_chunk(data.get("chunk", ""))

            elif event_type == "tool_result":
                content = data.get("content", "")
                is_err = content.startswith("Error") or "Exit code: 1" in content or "Exit code: 2" in content
                print_tool_result(data.get("name", "?"), content, is_error=is_err)

            elif event_type == "done":
                if self._streaming_text:
                    print_assistant_end()
                    self._streaming_text = False
                cid = data.get("conversation_id")
                if cid:
                    self.conversation_id = cid

            elif event_type == "error":
                if self._streaming_text:
                    print_assistant_end()
                    self._streaming_text = False
                print_error(data.get("message", "Unknown error"))

        def on_approval_needed(appr: dict) -> str:
            """Called from main thread when approval is required. Returns 'approve' or 'deny'."""
            print_approval_prompt(appr.get("tool_name", "?"), appr.get("args", {}))
            decision = get_approval_input()
            action = "approve" if decision in ("y", "yes") else "deny"
            if action == "approve":
                console.print(f"    [{COLORS['success']}]✓ Approved[/{COLORS['success']}]")
            else:
                console.print(f"    [{COLORS['warning']}]✗ Denied[/{COLORS['warning']}]")
            return action

        try:
            self.api.chat_stream(
                message=message,
                on_event=on_event,
                conversation_id=self.conversation_id,
                skill_id=self.skill_id if not self.conversation_id else None,
                on_approval_needed=on_approval_needed,
            )
        except Exception as e:
            if self._streaming_text:
                print_assistant_end()
                self._streaming_text = False
            print_error(str(e))

        if self._streaming_text:
            print_assistant_end()
            self._streaming_text = False

        print_separator()


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Nexus Terminal Client")
    parser.add_argument(
        "--url",
        default=os.environ.get("NEXUS_API_URL", "http://localhost:8002"),
        help="Backend API URL (default: http://localhost:8002)",
    )
    args = parser.parse_args()

    app = NexusTerminal(base_url=args.url)
    try:
        app.run()
    except KeyboardInterrupt:
        console.print(f"\n  [{COLORS['dim']}]Interrupted. Goodbye.[/{COLORS['dim']}]")
    finally:
        app.api.close()


if __name__ == "__main__":
    main()
