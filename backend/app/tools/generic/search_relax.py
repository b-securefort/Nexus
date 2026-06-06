"""Shared query-relaxation helper for keyword-search tools.

GitHub code/repo search and the Stack Exchange advanced-search API both AND
*every* term in the query, so a verbose natural-language phrase
("azure bicep aks module") frequently matches nothing even when a shorter form
("azure bicep aks") matches plenty. Tools call `relaxed_queries()` to get the
original query followed by progressively shorter prefixes to retry on zero
results, without changing their public JSON output shape.
"""


def relaxed_queries(query: str) -> list[str]:
    """Return the query plus progressively shorter prefixes to retry.

    The original query is always first. For queries longer than 3 words, up to
    two relaxation steps are appended (drop toward the first 3 words), capping
    total attempts at 3 so rate-limited APIs aren't hammered. Never relaxes
    below 3 words. Duplicates are removed while preserving order.
    """
    query = query.strip()
    terms = query.split()
    out = [query]
    if len(terms) <= 3:
        return out

    seen = {query}
    for keep in (max(3, len(terms) - 2), 3):
        candidate = " ".join(terms[:keep])
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out
