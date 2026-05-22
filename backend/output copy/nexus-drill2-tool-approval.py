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
    out_path = 'E:\\Work\\MyProjects\\Nexus\\backend\\output\\nexus-drill2-tool-approval.dot'
    with open(out_path, "w", encoding="utf-8") as _f:
        _f.write(self.dot.source)

def _capture_exit(self, exc_type, exc_value, traceback):
    self.render()
    _diagrams.setdiagram(None)

_diagrams.Diagram.render = _capture_render
_diagrams.Diagram.__exit__ = _capture_exit
# --- end capture header ---

from diagrams import Diagram, Cluster, Edge
with Diagram('Nexus Drill 2 - Tool execution & approval pipeline', direction='LR', graph_attr={'nodesep': '1.2', 'ranksep': '1.0'}, show=False):
    orch = AzureGeneric('Orchestrator\n(LLM returned tool_call)', azure_icon='policy')
    with Cluster('Tool dispatch pipeline (app/agent/concurrency.py)'):
        skill_check = AzureGeneric('Skill allowlist\n+ skill_name CV', azure_icon='conditional_access')
        sema = AzureGeneric('Per-user Semaphore(4)\n+ ThreadPool(64)', azure_icon='resource_group')
        arm_pre = AzureGeneric('ARM token preflight\n(JWT exp claim)', azure_icon='conditional_access')
        blocked = AzureGeneric('Blocked-prefix check\n(az account clear,\nad app/sp create/delete,\nrole assignment delete)', azure_icon='policy')
        approval = AzureGeneric('Approval gate /\nask_user pause\n(pending_approvals,\npending_questions)', azure_icon='conditional_access')
        subproc = AzureGeneric('subprocess.run\nenv allowlist (~14 keys)\nshell=False\nAZURE_ACCESS_TOKEN injected', azure_icon='automation')
    with Cluster('Result handling'):
        result = AzureGeneric('Tool result\n(status/output)', azure_icon='resource_group')
        summarise = AzureGeneric('LLM summariser\n(if >2 KB, non-error)', azure_icon='openai')
        learn_trigger = AzureGeneric('Success-after-failure\ndetector\n(triggers learning write)', azure_icon='ai_search')
    arm = AzureGeneric('Azure ARM / CLI', azure_icon='subscription')
    aoai = AzureGeneric('Azure OpenAI\n(summariser deployment)', azure_icon='openai')
    orch >> Edge(label='1 tool_call') >> skill_check
    skill_check >> Edge(label='2 allowed') >> sema
    sema >> Edge(label='3') >> arm_pre
    arm_pre >> Edge(label='4 ok / refresh') >> blocked
    blocked >> Edge(label='5 not blocked') >> approval
    approval >> Edge(label='6 approved') >> subproc
    subproc >> Edge(label='7 az + token CV') >> arm
    subproc >> Edge(label='8 stdout/stderr') >> result
    result >> Edge(label='9 if >2 KB') >> summarise >> Edge(label='10') >> aoai
    result >> Edge(style='dashed', label='11 back to orchestrator') >> orch
    result >> Edge(style='dashed', label='12 after retry success') >> learn_trigger