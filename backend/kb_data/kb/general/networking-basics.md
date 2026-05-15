# Networking Basics

A reference for foundational networking concepts used across teams.

## IP Addressing and Subnets

IP addresses identify devices on a network. IPv4 uses 32-bit addresses (e.g. `192.168.1.10`); IPv6 uses 128-bit.

**CIDR notation** expresses an address range: `10.0.0.0/24` means the first 24 bits are the network prefix, leaving 8 bits for hosts (254 usable addresses). Smaller prefix = larger range (`/16` = 65,534 hosts; `/28` = 14 hosts).

**Private ranges** (RFC 1918 — not routable on the public internet):

| Range | CIDR | Hosts |
|---|---|---|
| Class A | 10.0.0.0/8 | ~16 million |
| Class B | 172.16.0.0/12 | ~1 million |
| Class C | 192.168.0.0/16 | 65,534 |

**Subnet planning tip**: size subnets generously. Resizing a live subnet is painful. Leave a `/4` gap between subnets to allow future splitting.

## DNS

DNS maps hostnames to IP addresses. Resolution order (most OS defaults):

1. Local hosts file (`/etc/hosts` or `C:\Windows\System32\drivers\etc\hosts`)
2. Local DNS cache
3. Configured resolver (usually the corporate DNS server or gateway)
4. Recursive resolution via root → TLD → authoritative servers

**Key record types:**

| Type | Purpose |
|---|---|
| A | Hostname → IPv4 |
| AAAA | Hostname → IPv6 |
| CNAME | Alias to another hostname |
| MX | Mail routing |
| TXT | Verification, SPF, DKIM |
| SRV | Service location (port + hostname) |
| PTR | Reverse lookup (IP → hostname) |

**TTL** controls how long a record is cached. Low TTL (60 s) enables fast failover but increases resolver load. High TTL (3600 s) reduces load but slows propagation. For production records, 300 s is a common balance.

**Split DNS**: different answers for internal vs. external resolvers. Common for private endpoints — internal clients resolve to the private IP; external clients resolve to a public IP or see NXDOMAIN.

## Firewalls and Security Groups

A firewall filters traffic by matching rules (source IP, destination IP, port, protocol) against an ordered rule list. The first matching rule wins; a default-deny rule at the end blocks everything unmatched.

**Stateful** vs **stateless**:
- Stateful (most modern firewalls): tracks connection state; return traffic is automatically allowed.
- Stateless (e.g., AWS NACLs, some ACLs): each packet evaluated independently; you must explicitly allow both directions.

**Best practices**:
- Default deny on all inbound.
- Least privilege: allow only specific ports/sources needed.
- Separate rules for management (SSH/RDP) from application traffic — ideally restrict management to a bastion or VPN range.
- Log denied traffic; alert on unexpected spikes.

## VLANs

A VLAN (Virtual LAN) logically segments a physical switch into isolated broadcast domains. Devices on VLAN 10 cannot reach VLAN 20 without routing, even if they share the same physical hardware.

**Trunk ports** carry multiple VLANs between switches (tagged with 802.1Q headers). **Access ports** carry a single VLAN (untagged, for end devices).

Common uses: separate production, staging, management, IoT, and guest traffic at layer 2 without physical cabling changes.

## Routing

A router forwards packets between networks using a routing table. Each entry specifies: destination prefix, next hop, interface, and metric (cost).

**Route selection**: most specific prefix wins (longest prefix match). If two routes tie on specificity, administrative distance (protocol trust level) breaks the tie; if still tied, metric.

**Static routes**: manually configured, no overhead, fragile at scale.
**Dynamic routing (BGP, OSPF)**: routers exchange reachability information and converge on topology changes automatically.

**Default route** (`0.0.0.0/0`): catch-all — sends unmatched traffic to the gateway (internet or a firewall).

## Load Balancers

A load balancer distributes traffic across a pool of backend servers.

**Layer 4 (transport)**: routes by IP and TCP/UDP port. Fast; cannot inspect HTTP headers or cookies. Good for TCP pass-through, databases, gRPC.

**Layer 7 (application)**: routes by URL path, hostname, HTTP headers. Enables path-based routing (`/api` → service A, `/web` → service B), SSL termination, cookie-based session affinity, WAF integration.

**Health checks**: the LB regularly probes backends (HTTP 200, TCP connect, etc.). Unhealthy backends are removed from rotation. Always check that your health endpoint reflects real service health, not just "the port is open."

## VPN and Private Connectivity

**Site-to-site VPN**: encrypted tunnel between two networks (e.g. on-prem to cloud). Uses IPsec. Traffic flows over the public internet, encrypted. Suited for moderate bandwidth, lower cost.

**ExpressRoute / Direct Connect**: dedicated private circuit between on-prem and cloud, bypassing the public internet. Higher reliability, lower latency, predictable throughput — but more expensive and longer lead time.

**Point-to-site VPN**: individual client connects to a network (e.g. corporate VPN from a laptop). Uses TLS (SSTP, OpenVPN) or IKEv2.

**Latency matters**: for real-time workloads (voice, trading systems), measure round-trip time. VPN adds a few ms; ExpressRoute with co-location can be sub-millisecond.
