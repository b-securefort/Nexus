"""
Deterministic Azure acronym expansion for hybrid KB search.

Returns a deduplicated list [original_query, *expansions] capped at 6 terms.
Zero LLM calls — fast enough to run inline on every search request.
"""

from __future__ import annotations

# Maps each token (lowercase) to one or more expansion phrases.
# Single-letter abbreviations are intentionally excluded to avoid false positives.
_ACRONYM_MAP: dict[str, list[str]] = {
    # Compute / containers
    "aks":   ["kubernetes", "azure kubernetes service"],
    "vmss":  ["virtual machine scale set", "vm scale set"],
    "acr":   ["container registry", "azure container registry"],
    "aci":   ["container instances", "azure container instances"],
    "aca":   ["container apps", "azure container apps"],
    "avd":   ["azure virtual desktop", "virtual desktop"],
    "hpc":   ["high performance computing"],
    # Networking
    "vnet":  ["virtual network", "azure virtual network"],
    "nsg":   ["network security group"],
    "afd":   ["front door", "azure front door"],
    "agw":   ["application gateway", "azure application gateway"],
    "appgw": ["application gateway", "azure application gateway"],
    "waf":   ["web application firewall"],
    "natgw": ["nat gateway", "azure nat gateway"],
    "er":    ["expressroute", "azure expressroute"],
    "vpn":   ["vpn gateway", "azure vpn gateway"],
    "udr":   ["user defined route", "route table"],
    "pe":    ["private endpoint"],
    "pip":   ["public ip", "public ip address"],
    "lb":    ["load balancer", "azure load balancer"],
    # App services / integration
    "apim":  ["api management", "azure api management"],
    "asb":   ["service bus", "azure service bus"],
    "ase":   ["app service environment"],
    "asp":   ["app service plan"],
    "aas":   ["azure app service"],
    "func":  ["function app", "azure functions"],
    "lapp":  ["logic app", "azure logic apps"],
    # Storage / data
    "adls":  ["data lake storage", "azure data lake"],
    "adf":   ["data factory", "azure data factory"],
    "asa":   ["azure synapse analytics", "synapse"],
    "cosmos": ["cosmos db", "azure cosmos db"],
    "sqlmi": ["sql managed instance", "azure sql managed instance"],
    "sqldb": ["azure sql database"],
    # Identity / security
    "aad":   ["entra id", "azure active directory", "entra identity"],
    "rbac":  ["role assignment", "role based access control"],
    "msi":   ["managed identity", "managed service identity"],
    "mi":    ["managed identity"],
    "kv":    ["key vault", "azure key vault"],
    "pim":   ["privileged identity management"],
    "ca":    ["conditional access"],
    # Monitoring / ops
    "law":   ["log analytics workspace"],
    "appi":  ["application insights"],
    "ama":   ["azure monitor agent"],
    "dcr":   ["data collection rule"],
    # Governance
    "mg":    ["management group"],
    "rg":    ["resource group"],
    # AI / ML
    "aoai":  ["azure openai", "openai"],
    "ml":    ["machine learning", "azure machine learning"],
    "aml":   ["azure machine learning"],
    # Hybrid / other
    "arc":   ["azure arc"],
    "avs":   ["azure vmware solution"],
    "hci":   ["azure stack hci"],
}

_MAX_TERMS = 6


def expand_query(query: str) -> list[str]:
    """Return [query, *expansions], deduped, capped at _MAX_TERMS.

    Each token in the query is looked up independently.  The original
    query always comes first so BM25 can still match it verbatim.
    """
    tokens = query.lower().split()
    seen: set[str] = {query.lower()}
    result: list[str] = [query]

    for token in tokens:
        if token in _ACRONYM_MAP:
            for expansion in _ACRONYM_MAP[token]:
                if expansion not in seen:
                    seen.add(expansion)
                    result.append(expansion)
                    if len(result) >= _MAX_TERMS:
                        return result

    return result
