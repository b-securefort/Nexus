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
    out_path = 'E:\\Work\\MyProjects\\Nexus\\backend\\output\\nexus-drill3-kb-hybrid-retrieval.dot'
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
with Diagram('Nexus Drill 3 - KB hybrid retrieval', direction='TB', graph_attr={'nodesep': '1.5', 'ranksep': '1.2'}, show=False):
    kb_git = AzureGeneric('KB Git repo\n(ADO / GitHub)', azure_icon='devops')
    sync = AzureGeneric('git sync (15m)\n+ normalize markdown', azure_icon='automation')
    chunker = AzureGeneric('Chunker\n(split at H2/H3,\nKB_CHUNK_MAX_CHARS)', azure_icon='resource_group')
    embed_ingest = AzureGeneric('Azure OpenAI embed\ntext-embedding-3-small\n(1536 dims, document side)', azure_icon='openai')
    with Cluster('app.db (SQLite + sqlite-vec, WAL mode)'):
        chunks = SQLDatabases('kb_chunks\n(canonical: path,\nchunk_idx, heading, text,\ncontent_hash, source_url,\nembed_model)')
        fts = AzureGeneric('kb_chunks_fts\n(FTS5 virtual,\nunicode61 no porter)', azure_icon='ai_search')
        vec = AzureGeneric('kb_chunks_vec\n(vec0 virtual,\nfloat[1536])', azure_icon='ai_search')
    query_in = AzureGeneric('search_kb_hybrid\n(tool call from agent)', azure_icon='apim')
    embed_query = AzureGeneric('Azure OpenAI embed\n(query side, ~50 ms)', azure_icon='openai')
    bm25 = AzureGeneric('BM25 stage\n(FTS5 MATCH)', azure_icon='ai_search')
    vec_stage = AzureGeneric('Vector stage\n(vec0 MATCH ORDER BY distance)', azure_icon='ai_search')
    rrf = AzureGeneric('Reciprocal Rank Fusion\n(_rrf_fuse)', azure_icon='resource_group')
    results = AzureGeneric('Top-K chunks\n+ source_url cite', azure_icon='apim')
    kb_git >> Edge(label='1 pull') >> sync >> Edge(label='2 changed *.md') >> chunker
    chunker >> Edge(label='3 chunk text') >> embed_ingest >> Edge(label='4 vector') >> chunks
    chunks >> Edge(style='dashed', label='5 trigger') >> fts
    chunks >> Edge(style='dashed', label='6 vec0 insert') >> vec
    query_in >> Edge(label='A query text') >> embed_query
    embed_query >> Edge(label='B vec') >> vec_stage
    query_in >> Edge(label='C tokens') >> bm25
    bm25 >> Edge(label='D rank list') >> rrf
    vec_stage >> Edge(label='E rank list') >> rrf
    rrf >> Edge(label='F fused') >> results