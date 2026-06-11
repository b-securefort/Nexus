"""Dev helper: dump a conversation's message timeline (roles, tool calls,
advisory/error markers) for forensics. Usage: python scripts/dump_conv.py 360"""

import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

conv_id = int(sys.argv[1]) if len(sys.argv) > 1 else None
con = sqlite3.connect("app.db")
rows = con.execute(
    "select id, role, content, tool_calls_json, created_at from messages "
    "where conversation_id=? order by id", (conv_id,),
).fetchall()
print("messages:", len(rows))
for mid, role, content, tcj, ts in rows:
    calls = []
    if tcj:
        try:
            for c in json.loads(tcj):
                fn = c.get("function", {})
                name = fn.get("name", "?")
                extra = ""
                if name == "generate_structured_diagram":
                    a = json.loads(fn.get("arguments", "{}"))
                    n_edits = len(a.get("edits", []))
                    extra = "(full)" if "diagram" in a else f"(edits x{n_edits})"
                calls.append(name + extra)
        except Exception as ex:
            calls.append(f"?{ex}")
    low = (content or "").lower()
    markers = [(" [NO-STORED-IR]", "no stored ir"), (" [ERROR]", "error"),
               (" [ADVISORY]", "advisory"), (" [BACKWARD]", "backward-hop"),
               (" [FAR-HOP]", "far-hop"), (" [SCORE]", "scorecard")]
    flags = "".join(t for t, k in markers if k in low)
    print(mid, ts[11:19], role, calls or "", flags, "|",
          (content or "").replace("\n", " ")[:110])
