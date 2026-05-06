"""Verify $orderby=created desc works and returns 2026 items first."""
import httpx, json

HEADERS = {"User-Agent": "Mozilla/5.0 Chrome/124.0"}

with httpx.Client(timeout=20, follow_redirects=True) as c:
    # With $orderby=created desc
    r = c.get(
        "https://www.microsoft.com/releasecommunications/api/v2/azure",
        params={"$orderby": "created desc"},
        headers=HEADERS,
    )
    data = r.json()
    items = data.get("value", [])
    nextlink = data.get("@odata.nextLink", "")
    print(f"Page 1: {len(items)} items, nextLink host: {nextlink.split('/')[2] if nextlink else 'none'}")
    print("First 8 items (should be newest):")
    for i in items[:8]:
        print(f"  created={i.get('created','')[:10]}  modified={i.get('modified','')[:10]}  status={i.get('status','?'):<14} | {i.get('title','')[:60]}")

    print()
    # Verify page 2 via nextLink
    if nextlink:
        r2 = c.get(nextlink, headers=HEADERS)
        data2 = r2.json()
        items2 = data2.get("value", [])
        print(f"Page 2 via nextLink: {len(items2)} items, status={r2.status_code}")
        print("First 3 items of page 2:")
        for i in items2[:3]:
            print(f"  created={i.get('created','')[:10]}  {i.get('title','')[:65]}")
