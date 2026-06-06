"""Bundle registration — the seam that keeps core bundle-agnostic.

A **Bundle** is a directory under ``bundles/<name>/`` grouping the Tools for one
external platform or integration (DESIGN.md §5 2026-06-05). Each bundle's
``__init__.py`` calls :func:`register_bundle` with a :class:`Bundle` manifest
declaring its config flag and optional hooks that the loader and orchestrator
invoke generically — so core never imports a bundle by name.

Hooks:
  * ``prompt_fragment`` — STATIC system-prompt contribution (e.g. a tool
    hierarchy). Goes in the prompt cache-prefix, so it must be deterministic
    (no per-request data). Bundles are concatenated in sorted name order so the
    prefix stays byte-stable across turns.
  * ``context_prompt`` — DYNAMIC per-turn system-prompt contribution (e.g. the
    current Azure CLI login state). Placed after the static prefix.
  * ``on_tool_error`` — called with a tool's error result so the bundle can
    react (e.g. clear a cached auth state).
"""

from dataclasses import dataclass
from typing import Callable


def _empty_str() -> str:
    return ""


def _noop(result: str) -> None:
    return None


@dataclass
class Bundle:
    name: str
    config_flag: str
    prompt_fragment: Callable[[], str] = _empty_str
    context_prompt: Callable[[], str] = _empty_str
    on_tool_error: Callable[[str], None] = _noop


BUNDLE_REGISTRY: dict[str, Bundle] = {}


def register_bundle(bundle: Bundle) -> None:
    """Register a bundle manifest. Called from each bundle's ``__init__.py``."""
    BUNDLE_REGISTRY[bundle.name] = bundle


def bundle_prompt_fragments() -> str:
    """Concatenated STATIC prompt fragments from all enabled bundles, ordered by
    bundle name so the prompt-cache prefix is deterministic."""
    parts = [b.prompt_fragment() for _, b in sorted(BUNDLE_REGISTRY.items())]
    return "".join(p for p in parts if p)


def bundle_context_prompts() -> str:
    """Concatenated DYNAMIC per-turn context from all enabled bundles."""
    parts = [b.context_prompt() for _, b in sorted(BUNDLE_REGISTRY.items())]
    return "\n".join(p for p in parts if p)


def dispatch_tool_error(result: str) -> None:
    """Notify every enabled bundle of a tool error (auth-cache clears, etc.).
    A failing hook must never break the chat turn, so errors are swallowed."""
    for _, b in sorted(BUNDLE_REGISTRY.items()):
        try:
            b.on_tool_error(result)
        except Exception:  # pragma: no cover - defensive
            pass
