# Bugs reported by users
1. The Diagrams when rendered dont show the correct diagram in the chat, it shows an old diagram png still but the output file contains the right png and right diagram, this happens if there is a different diagram asked to be generated, it keeps showing the old diagram that got generated.
2. Learnings are not being captured properly
   1. firstly I dont see learnings being added so maybe the agent thinks there are no learnings by I can see based on recent conversations that it should have added something to learning
   2. Learnings table has empty values
3. Token usage shown below keeps resetting and in the end gives false token usage. Need to revisit the calculations.
4. Websearch, still doesnt give right searches, is it model fault or ddg fault, not sure. how can we improve this?
5. Draw io and Azure architect skills should allow to download the drawio file, but I cant download it from UI
6. What context az_cost_query is using
az_cost_query does not let me specify a subscription or billing scope directly. In this environment, it appears to query the default billing context tied to the logged-in account, not an arbitrary subscription scope. That’s why the output is a single total with breakdown by resource group, rather than “per subscription” totals.

What I verified
Logged-in account: balaji@futurefortifiedtech.com
Current Azure context in this session includes the subscription:
FFT-Dunamis Aviation - IaaS (3e40a1d8-c14c-434b-946a-dd0d1775e92f)
The cost query returns:
Total cost: $138.18
Grouped by ResourceGroup, not by subscription
What that means
The cost query is probably operating at one of these scopes:

Default billing scope for the user
A management group / billing account scope behind the scenes
A tenant-linked cost scope that isn’t pinned to the active subscription
So you were right to push back: I should not have implied that the result was a true subscription-wise breakdown.

The important limitation
Right now, this tool cannot directly parameterize:

subscriptionId
billing account scope
management group scope
So if your goal is:

“subscription-wise costs of each resource group”

the correct path is:

get the resource groups for each subscription from Resource Graph
get cost data from the current billing scope
map RG cost to subscription by RG membership
That works only if all RGs in the cost output belong to the same billing scope and the query is seeing all relevant RG spend.