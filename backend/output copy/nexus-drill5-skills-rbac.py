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
    out_path = 'E:\\Work\\MyProjects\\Nexus\\backend\\output\\nexus-drill5-skills-rbac.dot'
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
with Diagram('Nexus Drill 5 - Skills & RBAC', direction='TB', graph_attr={'nodesep': '1.5', 'ranksep': '1.2'}, show=False):
    user = Users('User')
    frontend = AzureGeneric('Frontend\n(MSAL acquireToken)', azure_icon='globe')
    entra = AzureGeneric('Entra ID\n(JWT + roles claim)', azure_icon='entra_id')
    with Cluster('Backend startup'):
        appconfig = AzureGeneric('App Configuration\nNexus:RoleAccessMap\n(JSON, read once)', azure_icon='app_config')
        defaults = AzureGeneric('Hardcoded defaults\napp/auth/rbac.py\n(fallback)', azure_icon='policy')
        access_map = AzureGeneric('_ACCESS_MAP\n(in-process,\nrole -> skills+tools)', azure_icon='resource_group')
    with Cluster('Per-request auth'):
        auth_mw = AzureGeneric('Auth middleware\nvalidate JWT\nextract User.roles', azure_icon='conditional_access')
    with Cluster('Filter points (3 enforcement boundaries)'):
        skills_api = AzureGeneric('GET /api/skills\n(visibility filter)', azure_icon='apim')
        tools_api = AzureGeneric('GET /api/tools\n(allow-list filter)', azure_icon='apim')
        personal_api = AzureGeneric('POST /api/skills/personal\n403 gate on tool save\n(non-negotiable)', azure_icon='apim')
    convo_create = AzureGeneric('POST /api/conversations\nfreeze skill_snapshot_json\n(invariant: snapshot wins)', azure_icon='apim')
    db = SQLDatabases('conversations.skill_snapshot_json\n+ personal_skills.tools_json')
    orch = AzureGeneric('Orchestrator\nresolves tools from snapshot,\nnot live skill', azure_icon='policy')
    user >> Edge(label='1 sign in') >> frontend >> Edge(label='2 token req') >> entra
    entra >> Edge(label='3 JWT (roles)') >> auth_mw
    appconfig >> Edge(label='0 @ startup') >> access_map
    defaults >> Edge(style='dashed', label='fallback') >> access_map
    auth_mw >> Edge(label='4 visibility') >> skills_api
    auth_mw >> Edge(label='5 allow-list') >> tools_api
    auth_mw >> Edge(label='6 save gate') >> personal_api
    skills_api >> Edge(label='7 user picks') >> convo_create
    convo_create >> Edge(label='8 freeze') >> db
    db >> Edge(label='9 resolve') >> orch