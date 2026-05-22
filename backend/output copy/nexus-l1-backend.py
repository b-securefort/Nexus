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
    out_path = 'E:\\Work\\MyProjects\\Nexus\\backend\\output\\nexus-l1-backend.dot'
    with open(out_path, "w", encoding="utf-8") as _f:
        _f.write(self.dot.source)

def _capture_exit(self, exc_type, exc_value, traceback):
    self.render()
    _diagrams.setdiagram(None)

_diagrams.Diagram.render = _capture_render
_diagrams.Diagram.__exit__ = _capture_exit
# --- end capture header ---

from diagrams import Diagram, Cluster, Edge
from diagrams.azure.database import SQLDatabases
from diagrams.onprem.client import Users
with Diagram('Nexus L1 - Backend Internals', direction='TB', graph_attr={'nodesep': '1.5', 'ranksep': '1.2'}, show=False):
    user = Users('User')
    frontend = AzureGeneric('Frontend\n(React + Vite)', azure_icon='globe')
    with Cluster('Backend (FastAPI, single process)'):
        api_chat = AzureGeneric('api/chat\n(SSE stream)', azure_icon='apim')
        orchestrator = AzureGeneric('Orchestrator\n(loop, <=15 iters)', azure_icon='policy')
        compaction = AzureGeneric('Compaction\n(preserve user msgs)', azure_icon='automation')
        learnings = AzureGeneric('Learnings retrieval\n(BM25 + vec + RRF)', azure_icon='ai_search')
        cb = AzureGeneric('Circuit breaker\n(closed/open/half_open)', azure_icon='policy')
        dispatch = AzureGeneric('Tool dispatch\nThreadPool(64) +\nSemaphore(4)/user', azure_icon='resource_group')
        approval = AzureGeneric('Approval gate /\nask_user pause', azure_icon='conditional_access')
        ro_tools = AzureGeneric('Read-only tools\nsearch_kb_hybrid, read_kb_file,\nms_docs, web_fetch,\naz_resource_graph,\naz_cost_query, az_monitor_logs', azure_icon='ai_search')
        mut_tools = AzureGeneric('Mutating tools\naz_cli, run_shell,\naz_rest_api (writes)', azure_icon='subscription')
        db = SQLDatabases('app.db\nSQLite + sqlite-vec\nmessages, conversations,\npending_approvals,\nkb_chunks*, agent_learnings*')
    aoai = AzureGeneric('Azure OpenAI\n(chat + embed)', azure_icon='openai')
    arm = AzureGeneric('Azure ARM / CLI', azure_icon='subscription')
    kb_git = AzureGeneric('KB Git repo\n(ADO / GitHub)', azure_icon='devops')
    user >> frontend >> Edge(label='SSE') >> api_chat >> orchestrator
    orchestrator >> Edge(label='history') >> compaction >> Edge(label='retrieve') >> learnings >> Edge(label='prompt') >> cb >> Edge(label='chat / embed') >> aoai
    orchestrator >> Edge(label='tool_calls') >> dispatch
    dispatch >> Edge(label='no approval') >> ro_tools
    dispatch >> Edge(label='needs approval') >> approval >> mut_tools
    mut_tools >> Edge(label='ARM preflight + token CV') >> arm
    kb_git >> Edge(style='dashed', label='reindex 15m') >> db