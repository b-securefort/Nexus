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
    out_path = 'E:\\Work\\MyProjects\\Nexus\\backend\\output\\nexus-drill1-chat-turn-lifecycle.dot'
    with open(out_path, "w", encoding="utf-8") as _f:
        _f.write(self.dot.source)

def _capture_exit(self, exc_type, exc_value, traceback):
    self.render()
    _diagrams.setdiagram(None)

_diagrams.Diagram.render = _capture_render
_diagrams.Diagram.__exit__ = _capture_exit
# --- end capture header ---

from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.client import Users
with Diagram('Nexus Drill 1 - Chat turn lifecycle', direction='TB', graph_attr={'nodesep': '1.5', 'ranksep': '1.2'}, show=False):
    user = Users('User')
    frontend = AzureGeneric('Frontend\n(MSAL + SSE consumer)', azure_icon='globe')
    with Cluster('Per chat turn (<= 15 LLM iters)'):
        api = AzureGeneric('api/chat (SSE)', azure_icon='apim')
        save = AzureGeneric('Save user msg\n+ attachments', azure_icon='resource_group')
        compact = AzureGeneric('Compaction\n(history -> bullets)', azure_icon='automation')
        learn = AzureGeneric('Learnings retrieval\n(BM25 + vec + RRF)', azure_icon='ai_search')
        sysprompt = AzureGeneric('System prompt build\n(KB summary + learnings\n+ ARM ctx + pinned task)', azure_icon='resource_group')
        cb = AzureGeneric('Circuit breaker', azure_icon='policy')
        dispatch = AzureGeneric('Tool dispatch\n+ approval/ask_user', azure_icon='conditional_access')
        tools = AzureGeneric('Tool execute\n(subprocess for az tools)', azure_icon='subscription')
        done = AzureGeneric('SSE done event\n+ usage payload', azure_icon='apim')
    aoai = AzureGeneric('Azure OpenAI\n(chat completions)', azure_icon='openai')
    user >> Edge(label='1 user msg') >> frontend >> Edge(label='2 SSE POST') >> api >> Edge(label='3') >> save
    save >> Edge(label='4') >> compact >> Edge(label='5') >> learn >> Edge(label='6') >> sysprompt
    sysprompt >> Edge(label='7 chat') >> cb >> Edge(label='8') >> aoai
    aoai >> Edge(label='9 tokens + tool_calls') >> dispatch
    dispatch >> Edge(label='10 execute') >> tools
    tools >> Edge(style='dashed', label='11 tool result\n(loop <=15)') >> sysprompt
    aoai >> Edge(style='dashed', label='12 final assistant msg') >> done
    done >> Edge(label='13 SSE done') >> frontend