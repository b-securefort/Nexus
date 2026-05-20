# Diagram authoring — start here

This is a router. There are **two** diagram authoring paths in Nexus; pick the
one that matches your skill.

## Python-based diagrams (preferred for the Azure Architect skill)

The Architect skill uses `generate_drawio_from_python`. You declare a graph in
Python (mingrammer/diagrams) and Graphviz lays it out; the tool emits an
editable `.drawio` plus a rendered `.png`. No manual coordinates, no validator
overlap loops.

**Read this**: [kb/python_diagrams/README.md](../python_diagrams/README.md)
— syntax, valid imports, when to use, hard rules (e.g. never
`from diagrams import AzureGeneric`).

**Examples**: [kb/python_diagrams/examples/](../python_diagrams/examples/)
— working Python sources you can adapt.

## Hand-written .drawio XML (Draw.io Diagrammer skill)

The Draw.io Diagrammer skill writes raw mxCell XML when the user needs an
exact layout, per-cell nudges via `patch_drawio_cell`, or post-edit in
diagrams.net.

**Read these**:
- [kb/drawio/REFERENCE.md](REFERENCE.md) — icon catalogs (`azureicons_drawio.txt`,
  `awsicons_drawio.txt`), refresh workflow.
- [kb/drawio/ms_reference_style.md](ms_reference_style.md) — Microsoft palette,
  container styles, connector styles, typography.
- [kb/drawio/patterns.md](patterns.md) — copy-paste XML fragments for badges,
  private endpoints, NSG corners, AZ columns, title blocks.
- [kb/drawio/azure_architecture_semantics.md](azure_architecture_semantics.md)
  — what each Azure component is, where it parents, what it must connect to.
- [kb/drawio/layoutfixing.md](layoutfixing.md) — worked examples of layout
  problems and their fixes.

## Common ambiguity

There is no file at `kb/drawio/README.md` other than this one. If the search
or the prompt mentions a "diagram README" without a path, this file is what
they mean — pick the section above that matches your skill.
