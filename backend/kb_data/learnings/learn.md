# Agent Learnings

This file records known issues, mistakes, and solutions discovered during tool execution.
The agent consults this before running commands to avoid repeating errors.

---
## [known-issue] Azure DevOps CLI requires proper authorization — TF400813
- **Date**: 2026-04-22 00:34 UTC
- **Tool**: az_devops
- **Details**: az_devops operations can fail with TF400813 if the current Azure identity lacks project access. Verify the Azure DevOps org/project and permissions before retrying. The tool requires the azure-devops CLI extension and a properly authorized identity or PAT.

## [best-practice] Use ResourceContainers (not Resources) for subscriptions and resource groups
- **Date**: 2026-04-21 22:59 UTC
- **Tool**: az_resource_graph
- **Details**: For listing subscriptions use `ResourceContainers | where type == 'microsoft.resources/subscriptions'`. For listing resource groups use `ResourceContainers | where type == 'microsoft.resources/subscriptions/resourcegroups'`. Querying `Resources` for these returns zero records.

## [syntax-fix] PowerShell script calling az must avoid pipeline execution of the az executable
- **Date**: 2026-04-22 02:00 UTC
- **Tool**: run_shell
- **Details**: A PowerShell script that used `az graph query -q $query -o json | ConvertFrom-Json` failed with `Cannot run a document in the middle of a pipeline: ...\az`. The fix is to assign the az output to a variable first (for example `$json = az graph query ... -o json`) and then pipe `$json | ConvertFrom-Json`, instead of piping the az executable directly.

## [syntax-fix] PowerShell cannot pipe az directly; capture output first
- **Date**: 2026-04-22 02:13 UTC
- **Tool**: run_shell
- **Details**: A PowerShell command that piped `az graph query ... | Out-String` failed with `Cannot run a document in the middle of a pipeline`. The correct approach is to assign the az output to a variable first (for example `$json = az graph query ... -o json`) and then process `$json`, rather than piping the az executable directly.

## [syntax-fix] Resource Graph subscription query should not project nonexistent state field
- **Date**: 2026-04-22 20:54 UTC
- **Tool**: az_resource_graph
- **Details**: A query against ResourceContainers for microsoft.resources/subscriptions failed when projecting `state` because that field is not present for subscription records in this environment. The corrected query was `ResourceContainers | where type == 'microsoft.resources/subscriptions' | project subscriptionId, name, tenantId | order by name asc`. Resource Graph was the fastest/simplest approach and should be preferred over Az CLI for listing subscriptions.

## [best-practice] For AI model counts, query deployment child resources first
- **Date**: 2026-04-22 21:40 UTC
- **Tool**: az_rest_api
- **Details**: When asked how many AI models are deployed across subscriptions, do not stop at listing AI hosting resources (Cognitive Services accounts or ML workspaces). The correct first pass is to enumerate Microsoft.CognitiveServices/accounts/{account}/deployments for each OpenAI/AIServices account and count the deployment child resources, separating Succeeded/Enabled from Disabled. Resource Graph is useful for finding the parent accounts, but it does not reliably expose the deployment layer.

## [best-practice] Return the actual AI deployment inventory directly when asked for deployed models
- **Date**: 2026-04-24 01:02 UTC
- **Tool**: az_rest_api
- **Details**: When users ask how many AI models are deployed, do not stop at counting parent Azure AI / Cognitive Services accounts or ML workspaces. Query the deployment child resources for each account first, then present a deployment-level table including account, model name, region, and provisioning status. Explicitly separate active/succeeded deployments from disabled ones so the answer matches the user's intent on the first pass.

## [workaround] Use parent AI account properties when ARG does not surface child deployment resources
- **Date**: 2026-04-24 01:29 UTC
- **Tool**: az_resource_graph
- **Details**: Resource Graph only returned parent Microsoft.CognitiveServices/accounts and Microsoft.MachineLearningServices/workspaces records even though deployed AI models are likely present as child resources or service-side objects. When ARG does not enumerate child deployments, inspect the parent resource properties for endpoints, account kind, and related workspace metadata, then use service-specific APIs/CLI only if a true deployment count is needed.

## [workaround] Add a 5-second delay between back-to-back cost API calls to avoid 429 rate limits
- **Date**: 2026-04-24 19:21 UTC
- **Tool**: az_cost_query
- **Details**: When using az_cost_query for multiple related cost queries, immediate consecutive calls can trigger HTTP 429 Too Many Requests. The reliable workaround is to wait about 5 seconds before issuing the next query, especially when querying adjacent time windows or multiple group-bys for the same subscription. This is preferable to retrying instantly.

## [known-issue] Azure DevOps project and work item operations can fail due to Conditional Access blocking token issuance
- **Date**: 2026-04-26 01:07 UTC
- **Tool**: az_devops
- **Details**: Attempting to list Azure DevOps projects failed first because the Azure DevOps CLI extension/auth was not initialized, and a REST fallback then failed with AADSTS53003 Conditional Access blocking token issuance. The correct next step is to authenticate the environment with `az login --scope https://management.core.windows.net//.default` (or equivalent approved auth path) before retrying az devops or REST calls.

## [gotcha] draw.io validator can misclassify resource-sized vertices unless Azure2 image styles are explicit and spaced apart
- **Date**: 2026-05-06 02:15 UTC
- **Tool**: generate_file
- **Details**: When generating Azure draw.io diagrams, generic-looking image vertices can still be flagged as generic styles by the validator. The safest approach is to use explicit Azure2 image paths for every resource icon, keep observability services fully outside VNets/VPNs, and give large spacing between resources and monitoring nodes to avoid overlap heuristics.

## [best-practice] Validator hints are non-blocking but almost always worth fixing
- **Date**: 2026-05-07 UTC
- **Tool**: validate_drawio
- **Details**: The validator now emits two kinds of feedback: blocking `[violation]` items that fail validation (overlap, parenting, observability-in-VNet, etc.) and non-blocking `[hint]` items that flag visual or architectural issues the strict rules cannot catch. Hints include: badge/edge-label collisions, badges floating in empty space far from any resource, Managed Identity inside a VNet, Private DNS zone inside a subnet, PaaS services (App Service, Key Vault, Cosmos, etc.) inside a subnet. A diagram with hints is structurally valid but visually or architecturally suboptimal. Address every hint unless there's a specific reason not to - they are the cheapest signal of "this won't look right" and "this is architecturally wrong" the agent gets without the user having to point it out.

## [best-practice] After validation, render the .drawio to PNG with render_drawio and visually review
- **Date**: 2026-05-07 UTC
- **Tool**: render_drawio
- **Details**: The new `render_drawio` tool calls the locally installed draw.io desktop CLI (Windows path: `C:\Program Files\draw.io\draw.io.exe`) to export a `.drawio` file to PNG. After validation passes and hints are addressed, ALWAYS render the diagram and visually review the PNG. The rendering catches issues neither the structural validator nor architectural hints can: orthogonal-edge router decisions that drop labels in unexpected places, multi-line labels that get truncated, stylistic problems with bidirectional arrows, etc. If `render_drawio` reports that draw.io is not installed, fall back to reasoning over the XML and the hints; otherwise, render and review every time. Treat "agent claims diagram is done without rendering" as incomplete output.

## [best-practice] Position auxiliary zones near the resources they relate to - not at the opposite end of the canvas
- **Date**: 2026-05-07 UTC
- **Tool**: generate_file
- **Details**: When a monitoring zone, identity zone, or DNS zone connects to a resource via an edge, place that zone NEAR the resource, not on the opposite side of the canvas. Long diagonal edges trigger draw.io's orthogonal router to pick paths through unrelated busy areas, and any edge label drops into that busy area causing label collisions with existing icon labels. Concrete rule: the monitoring zone for spoke telemetry goes directly below the spoke, not below the hub. The identity zone (Entra/MI) goes near the resource that uses MI, not at the far edge. For long edges that genuinely have to cross the canvas (e.g. private DNS zone with VNet links to both hub and spoke), either omit the edge label (the dashed style already conveys intent) or add explicit waypoints via `<Array as="points"><mxPoint x="..." y="..."/></Array>` inside `<mxGeometry relative="1">` to control routing. Validator does not catch these label collisions; only visual review does.

## [best-practice] Always do a visual review after validation passes — the validator catches structure, not communication quality
- **Date**: 2026-05-07 UTC
- **Tool**: generate_file
- **Details**: A `Validation PASSED` report does not mean the diagram looks good. The validator catches: encoding errors, missing icons, overlapping resources, container-padding violations, observability inside VNets, duplicate edge labels, and resource-parent mismatches. It does NOT catch: edge labels dropped in busy areas (label collisions), bidirectional arrows that are visually ambiguous, public-IP-without-association-to-its-NIC, NVAs drawn as floating icons without their service-chain context, badge positions that don't visibly anchor to a flow step, or zones placed at the wrong end of the canvas. After every PASSED validation, do a quick visual reasoning pass: (1) is every edge label readable and not overlapping anything? (2) is every numbered badge positioned next to the connector or icon it annotates? (3) does every arrow tell an unambiguous architectural story? (4) are PaaS/PE/MI/DNS placements consistent with `kb/drawio/azure_architecture_semantics.md`? Iterate on the file (with overwrite=true) for visual issues, even if structural validation has already passed.

## [best-practice] NVA inspection chains: use one bidirectional edge labelled as a hairpin, not ambiguous unidirectional arrows
- **Date**: 2026-05-07 UTC
- **Tool**: generate_file
- **Details**: When a load balancer (F5, AppGW) hairpins traffic to a firewall (Palo Alto, Azure Firewall) for L7 inspection and back, draw a single bidirectional edge between LB and firewall labelled "L7 inspection (hairpin)" with `endArrow=classic;startArrow=classic`. Two separate one-way arrows are ambiguous about ordering and clutter the LB subnet. For a Public IP attached to an NVA, draw the PIP icon adjacent to the NVA inside the same subnet (not at canvas level), and connect them with a thin no-arrow dashed line labelled "frontend IP" so the association is explicit — this matches the visual convention used by Microsoft Learn reference diagrams. Note in the legend that real production NVAs span untrust + trust subnets with separate NICs; the single-icon-per-NVA representation is a high-level simplification.

## [best-practice] When a diagram request matches a canonical Azure pattern, start from the reference example
- **Date**: 2026-05-07 UTC
- **Tool**: generate_file
- **Details**: `kb/drawio/examples/` contains pre-built `.drawio` files for common Azure patterns. They already pass validation and reflect correct architectural placement (Web App as PaaS outside the VNet, Managed Identity at canvas level, Private DNS zone with VNet Links, etc.). When a user's request matches one of these patterns, read the example with `read_kb_file` and adapt it (rename, add/remove components, adjust labels) rather than regenerating from scratch. Regenerating is the slow path and tends to reproduce past architectural mistakes. Currently available: `pattern_c_frontdoor_hub_f5_nat_spoke_pe.drawio` for any "AFD + hub firewall/LB + private spoke origin" request.

## [best-practice] Verify architectural correctness, not just visual style, when generating Azure diagrams
- **Date**: 2026-05-07 UTC
- **Tool**: generate_file
- **Details**: A diagram that passes validate_drawio and looks Microsoft-style can still be architecturally wrong. Before adding any "secure-looking" icon (Managed Identity, Private Endpoint, Private DNS zone, Key Vault), consult `kb/drawio/azure_architecture_semantics.md` and confirm: (1) the component is at the right plane — subnet-resident vs PaaS vs control-plane, (2) its parent container is correct, (3) it actually connects to something it talks to, (4) it has a reason to be in this specific diagram. Common mistakes from past runs: putting Managed Identity inside a subnet (it's an Entra ID object, not a network resource), placing a Private Endpoint in the same subnet as the PaaS service it exposes (PEs go in the consuming subnet, never colocated with the target), drawing Private DNS zones inside a "Private DNS subnet" (zones are regional, linked to VNets via VNet Links — they don't live in subnets), modelling Front Door as routing through a customer's hub firewall (AFD reaches its origin via public internet or Private Link only — there are valid hybrid patterns where AFD's origin is a public IP in the hub that NATs to a spoke PE, but this needs the F5/AppGW to be the origin endpoint, not a midpoint). When the user asks for "Front Door + hub-spoke", pick exactly one of the three documented reference patterns (Pattern A: AFD Premium + Private Link to spoke PE, Pattern B: AFD → public hub WAF/NVA → spoke, Pattern C: AFD → hub F5 public VIP → NAT → spoke PE) and model its components correctly — do not blend patterns.

## [best-practice] Plan draw.io layout coordinates BEFORE writing XML — never iterate by trial and error
- **Date**: 2026-05-07 UTC
- **Tool**: generate_file
- **Details**: The validate_drawio checks (overlap ≥80px horizontal / ≥60px vertical, containment ≥40px from edges) are deterministic and correct — they are not "false positives". When writing a Microsoft-style diagram, sketch every container's bounding box and every icon's coordinates on a 10px grid first, verify pairwise non-overlap and ≥40px container padding mathematically, and only then emit the XML. Iterating regenerate-and-revalidate cycles wastes budget and produces visually crowded diagrams. If you have written XML and the validator complains, fix the *spacing* — do not blame the validator and do not remove required visual elements (numbered badges, NSG corners) to silence it. The standard for "Microsoft reference architecture style" includes numbered badges; producing a diagram without them is incomplete output, not a workaround.

## [gotcha] generate_file truncation produces a confusing "filename required" / "received keys: (none)" error
- **Date**: 2026-05-07 UTC
- **Tool**: generate_file
- **Details**: If the model's response hits its token limit while emitting a large `content` argument, the JSON arguments get cut off mid-string and fail to parse — the tool then receives an empty dict and reports a misleading missing-parameter error. The fix is NOT to change parameter names or simplify the schema. The fix is: detect a "JSON failed to parse" or "received keys: (none)" error after a large write attempt, then either (a) shorten the payload by writing a compact skeleton first and rewriting with overwrite=true to add detail, or (b) write the diagram in two passes — title + containers + a few core icons first, then overwrite with the full version. Do not re-emit the same oversized payload — it will fail the same way.

## [known-issue] Do not place a second ingress gateway on the internet path when the intended entry point is hub F5
- **Date**: 2026-05-07 00:59 UTC
- **Tool**: generate_file
- **Details**: In a hub-and-spoke ingress design where the user explicitly says all internet traffic should enter through the F5 load balancer, the diagram must not show a separate Application Gateway outside the spoke as the first hop. The correct flow is Internet -> hub F5 public VIP (optionally via an upstream edge service only if requested) -> spoke Application Gateway/WAF -> Web App. If App Gateway is present, it should be inside the spoke and not drawn as an independent internet entry point.

## [best-practice] Add explicit subnet icons to subnet containers for Microsoft-style Azure network diagrams
- **Date**: 2026-05-07 01:03 UTC
- **Tool**: generate_file
- **Details**: When drawing hub and spoke VNets with multiple subnets, add a subnet icon inside each subnet container near the top-left label area. This improves readability and aligns with Microsoft reference architecture styling. Keep the subnet icon anchored within the subnet box, and continue using the Azure2 networking/Subnet.svg asset rather than a generic rectangle or unlabeled box.

## [best-practice] Model App Service VNet integration with a dedicated integration subnet rather than placing the Web App inside the VNet
- **Date**: 2026-05-07 01:05 UTC
- **Tool**: generate_file
- **Details**: When a Web App needs VNet integration, keep the Web App icon outside the VNet because App Service is still PaaS. Add a dedicated integration subnet in the spoke VNet and connect the Web App to that subnet with a clearly labeled association (e.g. VNet integration or outbound integration). Do not parent the Web App to the subnet; it remains a control-plane PaaS resource.

## [best-practice] Always render the validated draw.io diagram to PNG and review the image for layout feedback
- **Date**: 2026-05-07 01:08 UTC
- **Tool**: generate_file
- **Details**: After structural validation passes or is close to passing, render the .drawio file to PNG and inspect the actual image. This catches issues the validator cannot: edge labels colliding with icons, badges drifting into busy areas, and container boxes that technically validate but still look too cramped. The render step should be part of the normal feedback loop, not optional.

## [gotcha] real entry

## [gotcha] real entry

## [gotcha] real entry

## [syntax-fix] az login on Linux without browser
- **Date**: 2026-05-09 01:11 UTC
- **Tool**: az_cli
- **Details**: Use --use-device-code; bare 'az login' tries to spawn a browser and hangs in headless containers.

## [gotcha] validator vertex-size threshold
- **Date**: 2026-05-09 01:11 UTC
- **Tool**: validate_drawio
- **Details**: validate_drawio classifies vertices >= 300px wide or tall as containers. Stay under 280px for resource icons and the classification is correct.

## [gotcha] real entry

## [syntax-fix] az login on Linux without browser
- **Date**: 2026-05-09 01:13 UTC
- **Tool**: az_cli
- **Details**: Use --use-device-code; bare 'az login' tries to spawn a browser and hangs in headless containers.

## [gotcha] validator vertex-size threshold
- **Date**: 2026-05-09 01:13 UTC
- **Tool**: validate_drawio
- **Details**: validate_drawio classifies vertices >= 300px wide or tall as containers. Stay under 280px for resource icons and the classification is correct.

## [syntax-fix] let-bindings unsupported
- **Date**: 2026-05-09 01:18 UTC
- **Tool**: az_resource_graph
- **Details**: Resource Graph KQL does not support 'let' bindings; inline the values directly.

## [known-issue] azure-devops extension
- **Date**: 2026-05-09 01:18 UTC
- **Tool**: az_devops
- **Details**: az_devops requires the azure-devops CLI extension; install with 'az extension add --name azure-devops'.

## [best-practice] Cost API daily granularity
- **Date**: 2026-05-09 01:18 UTC
- **Tool**: az_cost_query
- **Details**: The Cost Management API caps daily granularity at 365 days; queries beyond return an error.

## [syntax-fix] az login on Linux without browser
- **Date**: 2026-05-09 01:18 UTC
- **Tool**: az_cli
- **Details**: Use --use-device-code; bare 'az login' tries to spawn a browser and hangs in headless containers.

## [gotcha] validator vertex-size threshold
- **Date**: 2026-05-09 01:18 UTC
- **Tool**: validate_drawio
- **Details**: validate_drawio classifies vertices >= 300px wide or tall as containers. Stay under 280px for resource icons and the classification is correct.

## [gotcha] real entry

## [syntax-fix] let-bindings unsupported
- **Date**: 2026-05-09 01:20 UTC
- **Tool**: az_resource_graph
- **Details**: Resource Graph KQL does not support 'let' bindings; inline the values directly.

## [known-issue] azure-devops extension
- **Date**: 2026-05-09 01:20 UTC
- **Tool**: az_devops
- **Details**: az_devops requires the azure-devops CLI extension; install with 'az extension add --name azure-devops'.

## [best-practice] Cost API daily granularity
- **Date**: 2026-05-09 01:20 UTC
- **Tool**: az_cost_query
- **Details**: The Cost Management API caps daily granularity at 365 days; queries beyond return an error.

## [syntax-fix] az login on Linux without browser
- **Date**: 2026-05-09 01:20 UTC
- **Tool**: az_cli
- **Details**: Use --use-device-code; bare 'az login' tries to spawn a browser and hangs in headless containers.

## [gotcha] validator vertex-size threshold
- **Date**: 2026-05-09 01:20 UTC
- **Tool**: validate_drawio
- **Details**: validate_drawio classifies vertices >= 300px wide or tall as containers. Stay under 280px for resource icons and the classification is correct.

## [gotcha] Subnets can be misclassified as resource-sized vertices if their dimensions or icon usage are not unmistakably container-like
- **Date**: 2026-05-09 22:12 UTC
- **Tool**: validate_drawio
- **Details**: When building Azure draw.io diagrams, the validator may flag subnet boxes as generic/resource-like if they are not clearly container-shaped and sufficiently distinct from resource icons. The safer approach is to keep subnet containers visually dominant with explicit subnet styling, give them more separation, and avoid placing resource icons too close to their borders. If a subnet is intended as a container, ensure its geometry and spacing make it obvious; otherwise the validator can misclassify it and report icon-style violations.

## [best-practice] For drawio: never default-assume a backend or access pattern; call ask_user first
- **Date**: 2026-05-10 02:45 UTC
- **Tool**: ask_user
- **Details**: When a drawio request leaves the backend service or the access pattern unspecified (e.g. "draw an Application Gateway in spoke" with no backend named), do NOT pick "a sensible default" and write XML. Call `ask_user` with multiple-choice questions for backend type and access pattern first. Producing a syntactically valid diagram of the wrong architecture wastes far more turns than the single round-trip an `ask_user` call costs. The drawio-diagrammer SKILL.md Step 0 is binding; treat it as a hard precondition, not advice.

## [gotcha] Narrating a file change without calling the write tool is a hallucinated success
- **Date**: 2026-05-10 03:05 UTC
- **Tool**: generate_file
- **Details**: Saying "I added X to the diagram" or "I patched the file" without actually calling `generate_file` or `patch_drawio_cell` in the same response leaves the file unchanged. The user will (rightly) call this out. A `read_kb_file` call is preparation, not a change. After reading whatever you need, you MUST follow up with the write tool in the same turn before claiming the change is done. Applies equally to follow-up requests on an existing diagram — those go straight to `patch_drawio_cell` or `generate_file overwrite=true`, not to ask_user.

## [gotcha] Follow-up edits on an existing diagram must skip ask_user
- **Date**: 2026-05-10 03:05 UTC
- **Tool**: ask_user
- **Details**: ask_user is for clarifying the FIRST message about a new diagram. Once a diagram exists in the conversation, follow-ups like "add a Key Vault", "include the hub abstraction", "move the App Gateway" are direct edit commands — answer them with patch_drawio_cell or generate_file overwrite=true, not with another ask_user call. Re-asking on a follow-up wastes a round-trip and confuses the user.

## [best-practice] Keep Private DNS zones in the hub by default unless the user specifies a spoke DNS zone
- **Date**: 2026-05-09 23:04 UTC
- **Tool**: generate_file
- **Details**: When a hub-spoke architecture includes a Private DNS zone, place it in the hub by default and show the VNet link(s) there. Do not place a Private DNS zone in the spoke unless the user explicitly asks for spoke-local DNS. This keeps the DNS resource centralized and avoids duplicating the zone unnecessarily in the spoke.

## [best-practice] Model WAF policy as an attached policy object, not a traffic hop
- **Date**: 2026-05-09 23:35 UTC
- **Tool**: generate_file
- **Details**: In Azure diagrams, the Web Application Firewall policy icon is a policy attachment to Application Gateway, not a network hop. Draw only an association between WAF policy and Application Gateway; do not route traffic through the WAF policy. The Application Gateway itself remains the traffic-processing component and forwards traffic to its backends.

## [gotcha] generate_python_diagram "Graphviz not found" usually means stale backend PATH, not missing install
- **Date**: 2026-05-14 00:30 UTC
- **Tool**: generate_python_diagram
- **Details**: If `generate_python_diagram` reports Graphviz `dot` is missing, do NOT assume Graphviz needs to be installed. The common cause is that Graphviz was installed AFTER the backend process started, so the backend's environment is using a stale PATH. The tool defensively prepends `C:\Program Files\Graphviz\bin` on Windows when `dot.exe` is present there, so on a current build this error means `dot` is genuinely absent from that standard location too. Check the install first (`Test-Path "C:\Program Files\Graphviz\bin\dot.exe"`) before recording a learning or asking the user to install anything. Restarting the backend resolves stale-PATH cases.

## [gotcha] Stop iterating when the user signals acceptance — don't manufacture problems
- **Date**: 2026-05-15 12:00 UTC
- **Tool**: generate_drawio_from_python, generate_file
- **Details**: The Phase 5 "review and invite critique" pattern from the collaborative diagramming skill becomes a UX failure if the agent keeps finding new problems after the user signals acceptance. Acceptance signals include: "ship it", "enough", "just create / generate / make it", "go ahead with what you have", "good enough", "stop iterating", "looks fine". When you see any of those, stop. Make at most one final tool call (or zero if the latest file is already the one to ship), then respond with only: one sentence on what the diagram shows + the file path. Do NOT add "I'd still adjust X" or offer further iterations — that pattern exhausted the user across ~15 regenerations in one observed session. Also: cap unsolicited polish iterations at TWO per successful render. After the second, let the user drive. Real architectural corrections from the user reset the counter; pure layout preferences don't. And Phase 5 review should only flag issues that are visible and meaningful — overlapping labels you can SEE, an icon that's plain wrong, a flow that's misleading. Not generic "spacing could be tighter" critiques.

## [gotcha] mingrammer diagrams class names — common hallucinations to avoid
- **Date**: 2026-05-15 12:00 UTC
- **Tool**: generate_drawio_from_python, generate_python_diagram
- **Details**: The installed `diagrams` library has specific class names that the LLM tends to hallucinate variants of. Confirmed bad imports observed during a real session (each one wasted a tool call): `from diagrams.azure.network import Subnet` (use `Subnets` plural), `from diagrams.azure.management import Monitor` (use `from diagrams.azure.monitor import Monitor`), `from diagrams.azure.security import WAFPolicies` (no such class — use `AzureGeneric(azure_icon="waf_policy")`), `from diagrams import AzureGeneric` (it's injected by the tool, never imported), `from diagrams.azure.network import NSG` (use `NetworkSecurityGroupsClassic`). When a service isn't sure to be in the catalog, default to `AzureGeneric("Display Name", azure_icon="<kind>")` rather than guessing a class name that "looks right" — the Phase 4b table in the drawio-from-python SKILL.md lists every valid azure_icon kind. The SKILL.md now carries the guaranteed-good imports inline (Phase 4a) and a forbidden-imports list (Phase 4c); consult them rather than guessing.

## [gotcha] AzureGeneric kind aliases must match the emitter's _KIND_TO_SVG keys exactly
- **Date**: 2026-05-15 12:00 UTC
- **Tool**: generate_drawio_from_python
- **Details**: When you use `AzureGeneric("Foo", azure_icon="<kind>")`, the kind string must be a key in the emitter's _KIND_TO_SVG dict, otherwise the node falls back to a plain rectangle in drawio (not what the user expects). Observed in a real session where AzureGeneric with `azure_icon="app_service"` rendered as an empty box because that alias wasn't mapped. The mapping has been broadened; current valid kinds are listed in the SKILL.md Phase 4b table. If you need a service icon and the kind isn't listed, do NOT invent a kind — instead pick the closest one or fall through to the mingrammer class for the service.

## [best-practice] Diagramming is architect-to-architect collaboration, not order-taking — and the loop continues on every change
- **Date**: 2026-05-14 01:15 UTC
- **Tool**: generate_drawio_from_python, generate_file, ask_user
- **Details**: When the user requests a diagram, do NOT jump to generating output. The expected flow is: (1) research — read the relevant KB files and learnings, name them explicitly; (2) reflect — briefly tell the user what you understood, which KB sources you're drawing from, and which architectural choices are still open; (3) confirm — call `ask_user` with concrete multi-select options for every open decision (backend service, access pattern, hub presence, DNS strategy, monitoring/identity inclusion); (4) generate only after the user has answered; (5) review — describe what the rendered PNG actually shows and INVITE the user to confirm or redirect. Never say "the diagram is ready" — only the user decides acceptance. Never assume a default (backend, access pattern, topology) just because it's "common"; if the KB doesn't justify it, ask. **The loop repeats for EVERY user-requested change.** "Add a Key Vault", "add another VM with SQL in its own subnet", "move the SQL DB to another subnet", "make it hub-and-spoke" — each triggers another reflect → confirm → generate → review round. The only changes that skip ask_user are ones fully specified by the user's exact words: pure renames, coordinate-free cosmetic changes, or changes where the user has already named every decision in their message. This applies to all diagram skills, not just python_to_drawio.

