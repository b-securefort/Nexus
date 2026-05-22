# --- Auto-injected capture header ---
import os
import diagrams as _diagrams

class AzureGeneric(_diagrams.Node):
    """Mingrammer-compatible node for Azure services that don't ship with
    the diagrams library. In Graphviz it renders as a labelled rounded box;
    in drawio it is upgraded to the matching Azure2 SVG via the azure_icon
    hint (e.g. AzureGeneric("Bastion", azure_icon="bastions")).
    """
    _provider = "azure"
    _type = "generic"
    _icon_dir = None
    _icon = None

    def __init__(self, label="", *, azure_icon=None, **attrs):
        if azure_icon:
            attrs["azure_icon"] = azure_icon
        super().__init__(label, **attrs)

def _capture_render(self):
    out_path = 'E:\\Work\\MyProjects\\Nexus\\backend\\output\\nexus-drill4-learnings-defense.dot'
    with open(out_path, "w", encoding="utf-8") as _f:
        _f.write(self.dot.source)

def _capture_exit(self, exc_type, exc_value, traceback):
    self.render()
    _diagrams.setdiagram(None)

_diagrams.Diagram.render = _capture_render
_diagrams.Diagram.__exit__ = _capture_exit
# --- end capture header ---

from diagrams import Diagram, Cluster, Edge
with Diagram('Nexus Drill 4 - Learnings write defense + retrieval', direction='TB', graph_attr={'nodesep': '1.5', 'ranksep': '1.2'}, show=False):
    orch = AzureGeneric('Orchestrator\n(success-after-failure\ndetector)', azure_icon='policy')
    with Cluster('Write path (orchestrator-owned, no agent tool)'):
        derive = AzureGeneric('Derive raw learning\n(rule-based from\nfailure -> success delta)', azure_icon='resource_group')
        rephrase = AzureGeneric("Rephrase via LLM\nstrict 'no opinions' prompt", azure_icon='openai')
        gate1 = AzureGeneric('Gate 1: Regex\n_OVERRIDE_PATTERNS\n(ignore validator,\ntoo strict, skip check)', azure_icon='policy')
        gate2 = AzureGeneric('Gate 2: Name guard\n(GUIDs, env-specific\nresource names)', azure_icon='policy')
        gate3 = AzureGeneric('Gate 3: LLM judge\nfails closed\n(approve=False on error)', azure_icon='openai')
    rejected = AzureGeneric('Rejected entry\nstatus=rejected\n(audit only,\ncannot reactivate)', azure_icon='policy')
    with Cluster('Storage (app.db, sqlite-vec)'):
        learnings_tbl = AzureGeneric('agent_learnings\nstatus=provisional/active/\narchived/rejected\nvalidation_count, failure_count', azure_icon='ai_search')
        embed = AzureGeneric('Azure OpenAI embed\n(same deployment as KB)', azure_icon='openai')
        lvec = AzureGeneric('agent_learnings_vec\nfloat[1536]', azure_icon='ai_search')
        lfts = AzureGeneric('agent_learnings_fts\n(FTS5)', azure_icon='ai_search')
    with Cluster('Retrieval (per turn, into next system prompt)'):
        retrieve = AzureGeneric('retrieve_relevant_learnings\nBM25 + vec + RRF\n+ status/tool boosts', azure_icon='ai_search')
        prompt_inject = AzureGeneric('Inject [CANONICAL] / [PROVISIONAL]\nmarkers into system prompt\n(omitted if 0 matches)', azure_icon='resource_group')
        mark = AzureGeneric('mark_learning_outcome\nincr validation_count / failure_count\nauto-promote at 3, auto-archive at 3', azure_icon='automation')
    orch >> Edge(label='1 success after failure') >> derive
    derive >> Edge(label='2') >> rephrase
    rephrase >> Edge(label='3') >> gate1
    gate1 >> Edge(label='4 pass') >> gate2
    gate2 >> Edge(label='5 pass') >> gate3
    gate3 >> Edge(label='6 approve') >> learnings_tbl
    gate1 >> Edge(style='dashed', label='reject') >> rejected
    gate2 >> Edge(style='dashed', label='reject') >> rejected
    gate3 >> Edge(style='dashed', label='reject') >> rejected
    learnings_tbl >> Edge(label='7 inline embed (limit=1)') >> embed
    embed >> Edge(label='8') >> lvec
    learnings_tbl >> Edge(style='dashed', label='trigger') >> lfts
    lvec >> Edge(label='A') >> retrieve
    lfts >> Edge(label='B') >> retrieve
    retrieve >> Edge(label='C top-5') >> prompt_inject
    prompt_inject >> Edge(style='dashed', label='next turn') >> orch
    prompt_inject >> Edge(style='dashed', label='track usage') >> mark
    mark >> Edge(style='dashed', label='counter update') >> learnings_tbl