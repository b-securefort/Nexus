"""Nexus Terminal Client — rich display helpers."""

from __future__ import annotations

import json
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner
from rich import box

console = Console()

# ── Color scheme ──────────────────────────────────────────────────────────

COLORS = {
    "accent": "bright_cyan",
    "user": "bright_green",
    "assistant": "bright_cyan",
    "tool": "bright_yellow",
    "error": "bright_red",
    "dim": "dim white",
    "success": "bright_green",
    "warning": "bright_yellow",
    "command": "bright_magenta",
    "border": "cyan",
}


# ── Helpers ───────────────────────────────────────────────────────────────

def format_command(tool_name: str, args: dict) -> str:
    """Convert tool args into a human-readable command string."""
    if tool_name == "az_cli" and isinstance(args.get("args"), list):
        return f"az {' '.join(args['args'])}"
    if tool_name == "run_shell" and isinstance(args.get("command"), str):
        return args["command"]
    if tool_name == "az_resource_graph" and isinstance(args.get("query"), str):
        return args["query"]
    if tool_name == "search_kb" and isinstance(args.get("query"), str):
        return f"search: {args['query']}"
    if tool_name == "read_kb_file" and isinstance(args.get("path"), str):
        return args["path"]
    if tool_name == "fetch_ms_docs" and isinstance(args.get("query"), str):
        return f"docs: {args['query']}"
    if tool_name == "update_learnings":
        return f"[{args.get('category', 'learning')}] {args.get('summary', '')}"
    if tool_name == "read_learnings":
        return "read learn.md"
    # fallback
    filtered = {k: v for k, v in args.items() if k != "reason"}
    if not filtered:
        return ""
    return ", ".join(f"{k}: {v}" for k, v in filtered.items())


# ── Display functions ─────────────────────────────────────────────────────

def print_banner():
    banner = Text()
    banner.append("╔══════════════════════════════════════════════════╗\n", style=COLORS["border"])
    banner.append("║", style=COLORS["border"])
    banner.append("     ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗  ", style=COLORS["accent"])
    banner.append("║\n", style=COLORS["border"])
    banner.append("║", style=COLORS["border"])
    banner.append("     ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝  ", style=COLORS["accent"])
    banner.append("║\n", style=COLORS["border"])
    banner.append("║", style=COLORS["border"])
    banner.append("     ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗  ", style=COLORS["accent"])
    banner.append("║\n", style=COLORS["border"])
    banner.append("║", style=COLORS["border"])
    banner.append("     ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║  ", style=COLORS["accent"])
    banner.append("║\n", style=COLORS["border"])
    banner.append("║", style=COLORS["border"])
    banner.append("     ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║  ", style=COLORS["accent"])
    banner.append("║\n", style=COLORS["border"])
    banner.append("║", style=COLORS["border"])
    banner.append("     ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝  ", style=COLORS["accent"])
    banner.append("║\n", style=COLORS["border"])
    banner.append("║", style=COLORS["border"])
    banner.append("           Team Architect Assistant — Terminal      ", style="dim cyan")
    banner.append("║\n", style=COLORS["border"])
    banner.append("╚══════════════════════════════════════════════════╝", style=COLORS["border"])
    console.print(banner)
    console.print()


def print_skills(skills: list[dict]):
    table = Table(box=box.SIMPLE_HEAVY, border_style=COLORS["border"], show_header=True)
    table.add_column("#", style="bold white", width=3)
    table.add_column("ID", style=COLORS["dim"])
    table.add_column("Name", style=COLORS["accent"])
    table.add_column("Tools", style=COLORS["tool"])
    for i, s in enumerate(skills, 1):
        tools = ", ".join(s.get("tools", []))
        table.add_row(str(i), s["id"], s["display_name"], tools)
    console.print(table)


def print_conversations(conversations: list[dict]):
    if not conversations:
        console.print("  [dim]No conversations yet.[/dim]")
        return
    table = Table(box=box.SIMPLE_HEAVY, border_style=COLORS["border"], show_header=True)
    table.add_column("#", style="bold white", width=3)
    table.add_column("Title", style=COLORS["accent"], max_width=60)
    table.add_column("Skill", style=COLORS["dim"])
    table.add_column("Updated", style=COLORS["dim"])
    for i, c in enumerate(conversations, 1):
        table.add_row(str(i), c["title"], c.get("skill_id", ""), c.get("updated_at", "")[:16])
    console.print(table)


def print_user_message(text: str):
    console.print()
    console.print(f"  [bold {COLORS['user']}]▶ You:[/bold {COLORS['user']}] {text}")


def print_assistant_start():
    console.print(f"\n  [bold {COLORS['assistant']}]◀ Nexus:[/bold {COLORS['assistant']}]", end=" ")


def print_assistant_token(token: str):
    console.print(token, end="", highlight=False)


def print_assistant_end():
    console.print()  # newline


def print_tool_call_start(name: str, args: dict):
    cmd = format_command(name, args)
    console.print()
    console.print(f"  [bold {COLORS['tool']}]⚡ Tool call:[/bold {COLORS['tool']}] [bold]{name}[/bold]")
    if cmd:
        console.print(f"    [{COLORS['command']}]{cmd}[/{COLORS['command']}]")
    reason = args.get("reason")
    if reason:
        console.print(f"    [{COLORS['dim']}]Reason: {reason}[/{COLORS['dim']}]")


def print_tool_executing(name: str):
    console.print(f"    [{COLORS['warning']}]⏳ Executing {name}...[/{COLORS['warning']}]")


def print_tool_output_chunk(chunk: str):
    # Indent each line of output
    for line in chunk.splitlines(keepends=True):
        console.print(f"    [{COLORS['dim']}]│[/{COLORS['dim']}] {line}", end="", highlight=False)


def print_tool_result(name: str, content: str, is_error: bool = False):
    color = COLORS["error"] if is_error else COLORS["success"]
    status = "✗ Failed" if is_error else "✓ Done"
    console.print(f"    [{color}]{status}[/{color}]")
    if content and len(content) < 2000:
        # Show short results inline
        panel = Panel(
            content.strip(),
            title=f"{name} output",
            border_style="red" if is_error else "green",
            expand=False,
            width=min(console.width - 4, 100),
        )
        console.print(panel)
    elif content:
        # Truncate long output
        lines = content.strip().split("\n")
        preview = "\n".join(lines[:30])
        if len(lines) > 30:
            preview += f"\n... ({len(lines) - 30} more lines)"
        panel = Panel(
            preview,
            title=f"{name} output ({len(lines)} lines)",
            border_style="red" if is_error else "green",
            expand=False,
            width=min(console.width - 4, 100),
        )
        console.print(panel)


def print_approval_prompt(tool_name: str, args: dict) -> str:
    """Print approval prompt and return user decision."""
    cmd = format_command(tool_name, args)
    console.print()
    console.print(Panel(
        f"[bold]Tool:[/bold] {tool_name}\n"
        f"[bold]Command:[/bold] [{COLORS['command']}]{cmd}[/{COLORS['command']}]\n"
        f"[bold]Reason:[/bold] {args.get('reason', 'N/A')}",
        title="[bold yellow]⚠ Approval Required[/bold yellow]",
        border_style="yellow",
        expand=False,
        width=min(console.width - 4, 90),
    ))
    return ""  # caller handles input


def print_error(message: str):
    console.print(f"\n  [{COLORS['error']}]✗ Error: {message}[/{COLORS['error']}]")


def print_help():
    console.print(Panel(
        "[bold cyan]Commands:[/bold cyan]\n"
        "  [bold]/new[/bold]              — Start a new conversation\n"
        "  [bold]/skills[/bold]           — List available skills\n"
        "  [bold]/skill <id>[/bold]       — Switch skill\n"
        "  [bold]/history[/bold]          — List past conversations\n"
        "  [bold]/load <#>[/bold]         — Load a conversation\n"
        "  [bold]/clear[/bold]            — Clear terminal\n"
        "  [bold]/help[/bold]             — Show this help\n"
        "  [bold]/quit[/bold]             — Exit\n\n"
        "[bold cyan]During approval:[/bold cyan]\n"
        "  [bold]y / yes[/bold]           — Approve execution\n"
        "  [bold]n / no[/bold]            — Deny execution\n\n"
        "[dim]Type any message to chat. All tool executions require your approval.[/dim]",
        title="[bold]Nexus Terminal Help[/bold]",
        border_style=COLORS["border"],
        expand=False,
    ))


def print_separator():
    console.print(f"  [{COLORS['dim']}]{'─' * 60}[/{COLORS['dim']}]")


def print_conversation_loaded(title: str, msg_count: int, skill_id: str):
    console.print(f"  [{COLORS['accent']}]Loaded conversation:[/{COLORS['accent']}] {title}")
    console.print(f"  [{COLORS['dim']}]Messages: {msg_count} | Skill: {skill_id}[/{COLORS['dim']}]")
    print_separator()


def print_history_message(role: str, content: str, tool_calls_json: str | None = None):
    """Print a single historical message."""
    if role == "user":
        console.print(f"  [bold {COLORS['user']}]▶ You:[/bold {COLORS['user']}] {content[:200]}")
    elif role == "assistant":
        if content:
            console.print(f"  [bold {COLORS['assistant']}]◀ Nexus:[/bold {COLORS['assistant']}] {content[:300]}")
        if tool_calls_json:
            try:
                calls = json.loads(tool_calls_json)
                for c in calls:
                    fn = c.get("function", {})
                    name = fn.get("name", "?")
                    args = json.loads(fn.get("arguments", "{}"))
                    cmd = format_command(name, args)
                    console.print(f"    [{COLORS['tool']}]⚡ {name}[/{COLORS['tool']}] {cmd[:80]}")
            except (json.JSONDecodeError, KeyError):
                pass
    elif role == "tool":
        pass  # tool results shown via tool call cards
