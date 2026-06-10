"""sleep — pause the agent loop for a bounded number of seconds.

Rate-limited tools tell the model to "please wait" (the per-user tool rate
limiter, Azure 429s, the Cost API throttle), but the model had no way to
actually wait — it either abandoned the approach, switched tools, or asked
the user to retry later. This gives it the obvious primitive: sleep out the
window, then retry the SAME action.

Runs on the dedicated tool executor (a sleeping thread occupies one of the
user's 4 concurrency slots, which is exactly the right backpressure), and is
capped per call so a confused model can't park a turn for minutes — for a
longer wait it must call again, which keeps each wait visible in the
conversation as its own step.
"""

import json
import logging
import time

from app.auth.models import User
from app.tools.base import Tool

logger = logging.getLogger(__name__)

MAX_SLEEP_SECONDS = 120.0


class SleepTool(Tool):
    name = "sleep"
    description = (
        "Pause for N seconds, then continue. Use when an action is RATE "
        "LIMITED — a tool result says 'Maximum N calls per X seconds', an API "
        "returns 429 / 'Too Many Requests', or a known throttle window applies "
        "(e.g. wait a few seconds between Azure Cost API calls). Sleep for the "
        "remaining window, then retry the SAME action — being throttled is not "
        "a reason to abandon the approach or switch tools. Max "
        f"{MAX_SLEEP_SECONDS:.0f}s per call; for a longer wait, call it again. "
        "Do NOT use it for anything except waiting out a throttle/propagation "
        "delay (e.g. an Azure resource that needs a moment to provision)."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "seconds": {
                "type": "number",
                "description": f"How long to sleep (1–{MAX_SLEEP_SECONDS:.0f} seconds).",
            },
            "reason": {
                "type": "string",
                "description": "One short phrase: what you are waiting for.",
            },
        },
        "required": ["seconds"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        try:
            seconds = float(args.get("seconds", 0))
        except (TypeError, ValueError):
            return "Error: seconds must be a number"
        if seconds <= 0:
            return "Error: seconds must be positive"
        clamped = min(seconds, MAX_SLEEP_SECONDS)
        reason = str(args.get("reason") or "").strip()
        logger.info("sleep tool: %.1fs (%s)", clamped, reason or "no reason given")
        time.sleep(clamped)
        note = (
            f"Slept {clamped:.0f}s"
            + (f" (requested {seconds:.0f}s, capped at {MAX_SLEEP_SECONDS:.0f}s — "
               "call again if you need to wait longer)" if seconds > clamped else "")
            + ". Now retry the rate-limited action."
        )
        return json.dumps({"status": "slept", "seconds": clamped,
                           **({"reason": reason} if reason else {}),
                           "note": note})
