"""Azure CLI execution base for the Azure bundle.

`AzureToolBase` and the `az` executable resolver (`_find_az`) used to live in
`app/tools/base.py` (core). They moved here so core contains no Azure-specific
code (DESIGN.md §5 2026-06-05). The generic subprocess utilities
(`SUBPROCESS_FLAGS`, `check_shell_injection`, `retry_with_backoff`) and the
per-request credential ContextVar accessor (`get_arm_token`) stay in core and
are imported below — the dependency points bundle → core, never the reverse.

Underscore-prefixed so `init_tools()`'s module scan skips it; it is imported
explicitly by the az tool modules that subclass `AzureToolBase`.
"""

import logging
import os
import shutil
import subprocess
import sys

from app.tools.base import (
    SUBPROCESS_FLAGS,
    Tool,
    check_shell_injection,
    get_arm_token,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)


_az_executable_path: str | None = None
_az_circuit_breaker_tripped: bool = False


def _find_az() -> str | None:
    """Resolve the full path to the Azure CLI executable.

    Implements a Circuit Breaker: if 'az' is missing, it trips the breaker
    and returns None, preventing expensive subprocess failures.
    """
    global _az_executable_path, _az_circuit_breaker_tripped

    if _az_circuit_breaker_tripped:
        return None

    if _az_executable_path:
        return _az_executable_path

    path = shutil.which("az")
    if not path and sys.platform == "win32":
        path = shutil.which("az.cmd")

    if path:
        _az_executable_path = path
        return path

    logger.error("Azure CLI Circuit Breaker TRIPPED: 'az' executable not found on system.")
    _az_circuit_breaker_tripped = True
    return None


# B1 — Explicit allowlist instead of inheriting the full process environment.
# This prevents credential-exfiltration attacks where a malicious az argument
# reads %AZURE_OPENAI_API_KEY% or similar secrets out of os.environ.  Only the
# vars required for az to function are forwarded; everything else is stripped.
# (§5 2026-05-21; module-level since §5 2026-06-13 so az_cli shares it.)
_ALLOWED_ENV_KEYS = {
    # Core path resolution
    "PATH", "PATHEXT",
    # Unix home (az stores config under ~/.azure)
    "HOME",
    # Windows profile root (az config dir default on Windows)
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    # Explicit az config override
    "AZURE_CONFIG_DIR",
    # Windows subsystem / temp
    "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP",
    # Proxy settings that az respects
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    # Needed by some az extension installers
    "AZURE_EXTENSION_DIR",
}


def _az_env() -> dict[str, str]:
    """Allowlisted environment for an az subprocess + the ARM token overlay.

    The overlay makes az authenticate as the current user: get_arm_token()
    reads the per-request ContextVar the orchestrator sets at the top of each
    chat turn (the accessor stays in core).
    """
    env: dict[str, str] = {
        k: v for k, v in os.environ.items() if k in _ALLOWED_ENV_KEYS
    }
    arm_token = get_arm_token()
    if arm_token:
        env["AZURE_ACCESS_TOKEN"] = arm_token
    return env


class AzureToolBase(Tool):
    """Base class for tools that call the az CLI under the hood."""

    # All az-backed tools authenticate via the per-request ARM token set by the
    # orchestrator. Reproduces the old `isinstance(tool, AzureToolBase)` check
    # (DESIGN.md §5 2026-06-05). NB: AzCliTool does NOT inherit this base, so —
    # matching prior behaviour — az_cli does not set requires_credentials.
    requires_credentials = True

    max_output_size = 12288  # Default 12KB token-budget friendly limit

    def _run_az(self, cmd: list[str], label: str, timeout: int = 60, use_retry: bool = True, truncate: bool = True) -> str:
        # Callers (each tool's execute()) are responsible for the login check.
        az_path = _find_az()
        if not az_path:
            return "Error: Azure CLI is not installed on this server. Circuit breaker is open. Tool disabled."

        cmd[0] = az_path

        # Defence-in-depth: Block shell injection in arguments
        for i, arg in enumerate(cmd[1:]):  # skip 'az' command itself
            injection_err = check_shell_injection(str(arg), f"cmd[{i+1}]")
            if injection_err:
                return injection_err

        env = _az_env()

        try:
            def _run():
                # CR#1 — Never pass shell=True on Windows.  shell=True passes
                # the full command as a string to cmd.exe which re-interprets
                # metacharacters (&, |, %, etc.) even inside quoted tokens.
                # Instead we always use shell=False and let subprocess call the
                # executable directly.  On Windows, _find_az() already resolves
                # 'az' to 'az.cmd' so subprocess can invoke it without a shell.
                return subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    shell=False,
                    env=env,
                    **SUBPROCESS_FLAGS,
                )

            if use_retry:
                result = retry_with_backoff(_run)
            else:
                result = _run()

            if result.returncode != 0:
                error = result.stderr.strip() if result.stderr else "Unknown error"
                return f"Error ({label}) [exit {result.returncode}]: {error}"

            output = result.stdout.strip()
            if truncate and len(output) > self.max_output_size:
                output = output[:self.max_output_size] + "\n... (truncated)"
            # Return empty string on empty success — callers decide how to phrase
            # "no results" so they can match each tool's original CLI semantics.
            return output

        except subprocess.TimeoutExpired:
            return f"Error: {label} timed out after {timeout} seconds"
        except FileNotFoundError:
            return "Error: Azure CLI (az) not found. Is it installed?"
        except Exception as e:
            logger.error("%s error: %s", label, str(e), exc_info=True)
            return f"Error: {str(e)}"
