import json
data = json.load(open("e2e_results.json"))
for r in data:
    if r["status"] == "ok":
        tools = [t["name"] for t in r["tool_calls"]]
        print(f"T{r['test_num']:2d} {r['duration_ms']:6d}ms tools={tools}")
        if r["assistant_text"]:
            print(f"     text={r['assistant_text'][:150]}...")
