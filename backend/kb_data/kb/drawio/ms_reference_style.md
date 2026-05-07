# Microsoft Reference Architecture Visual Style Guide

This guide defines the visual language used in Microsoft's published reference architecture diagrams (learn.microsoft.com). Use it to make draw.io output that matches that standard.

## The Goal

Produce diagrams that look like the ones on Microsoft Learn — clean, light, professional. The aesthetic is:
- White or very light backgrounds with subtle colored borders
- Official Azure2 service icons (coloured SVG)
- Numbered green badges showing traffic/data flow sequence
- Thin, dark, orthogonal arrows (not coloured by traffic type)
- Logical grouping boxes with labelled headers

---

## Container Styles

These are the exact style strings for each container type. Copy them directly.

### Virtual Network
```
rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;strokeWidth=2;dashed=1;dashPattern=8 4;fontStyle=1;fontSize=12;align=left;verticalAlign=top;spacingTop=8;spacingLeft=8;
```
The VNet box is the dominant structural element. Use a dashed blue border to signal "private network boundary". Pair it with a small VNet icon in the top-left corner (see Patterns doc).

### Subnet
```
rounded=0;whiteSpace=wrap;html=1;fillColor=#F0F7FF;strokeColor=#9BC2E6;strokeWidth=1;fontStyle=1;fontSize=11;align=left;verticalAlign=top;spacingTop=6;spacingLeft=6;
```
Subnets sit inside the VNet. Solid thin blue-gray border, slightly tinted white fill. Always include the subnet name in the label. Add a small NSG icon in the top-right corner when an NSG is attached (see Patterns doc).

### Azure Bastion Subnet (special — darker label)
Same as Subnet but label styled bold: `fontStyle=1;fontColor=#1A1A1A;`

### Resource Group
```
rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#AAAAAA;strokeWidth=1;dashed=1;dashPattern=6 4;fontStyle=1;fontSize=12;align=left;verticalAlign=top;spacingTop=8;spacingLeft=8;
```
Light gray, dashed. Used as the outermost envelope when showing a full deployment scope.

### Availability Zone column
```
rounded=0;whiteSpace=wrap;html=1;fillColor=#FAFAFA;strokeColor=#CCCCCC;strokeWidth=1;dashed=0;fontStyle=1;fontSize=11;align=center;verticalAlign=top;spacingTop=6;fontColor=#555555;
```
Columns labelled "Zone 1", "Zone 2", "Zone 3". Sit side by side inside the VNet or subscription box.

### Monitoring / Observability zone (always outside VNet)
```
rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#BBBBBB;strokeWidth=1;dashed=0;fontStyle=1;fontSize=11;align=left;verticalAlign=top;spacingTop=6;spacingLeft=8;
```
Light gray, solid border. Place this below or to the right of the VNet. Minimum height 140px.

### Phase label bar (for linear flow diagrams without VNets)
For diagrams like event-driven pipelines or migration flows, use a plain text label above each column of services:
```
text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;whiteSpace=wrap;fontStyle=1;fontSize=10;fontColor=#444444;
```

---

## Icon Style (Azure2)

Every Azure resource uses this base style — substitute the correct SVG path:
```
sketch=0;outlineConnect=0;fontColor=#1A1A1A;gradientColor=none;fillColor=#FFFFFF;strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;shape=image;image=img/lib/azure2/<category>/<Icon_Name>.svg;
```

Key points:
- `fillColor=#FFFFFF` and `strokeColor=none` — icons have a white background, no border
- `verticalLabelPosition=bottom` — label goes below the icon
- Size: 48×48 for secondary/decorative, 56×56 for primary resources
- The label uses the default font (Helvetica/Arial); keep it short (1–3 words)

---

## Connector (Arrow) Styles

Microsoft reference diagrams use **thin dark arrows**, not color-coded by traffic type. Color is used sparingly — only when the diagram specifically needs to distinguish flow types.

### Standard flow arrow (the default)
```
edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#444444;strokeWidth=1.5;endArrow=block;endFill=1;
```

### Private / internal connection (dashed)
Use for private endpoints, VNet peering, internal links:
```
edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#444444;strokeWidth=1;dashed=1;dashPattern=6 4;endArrow=block;endFill=1;
```

### Name resolution / DNS
```
edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#888888;strokeWidth=1;dashed=1;dashPattern=4 4;endArrow=open;endFill=0;
```

### Telemetry / diagnostics (to monitoring zone)
```
edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#999999;strokeWidth=1;dashed=1;dashPattern=4 4;endArrow=block;endFill=0;
```

Keep connector labels short: "HTTPS", "Private endpoint", "Logs", "Name resolution". Leave them off when the numbered badge already tells the story.

---

## Typography

| Element | Style |
|---|---|
| Diagram title | `fontStyle=1;fontSize=16;fontColor=#1A1A1A;` |
| Container label | `fontStyle=1;fontSize=11–12;` |
| Icon label (below icon) | Default, `fontSize=10–11;fontColor=#1A1A1A;` |
| Phase/column header | `fontStyle=1;fontSize=10;fontColor=#444444;` |
| Edge label | `fontSize=9–10;fontColor=#444444;` |

---

## Color Reference

| Use | Hex |
|---|---|
| VNet fill | `#EFF6FC` |
| VNet stroke | `#0078D4` |
| Subnet fill | `#F0F7FF` |
| Subnet stroke | `#9BC2E6` |
| Resource Group fill | `#F5F5F5` |
| Resource Group stroke | `#AAAAAA` |
| Zone column fill | `#FAFAFA` |
| Zone column stroke | `#CCCCCC` |
| Monitoring zone fill | `#F5F5F5` |
| Numbered badge (green) | `#107C10` |
| Numbered badge (orange, alt) | `#C55A11` |
| Default arrow | `#444444` |
| Secondary/dashed arrow | `#888888` |
| Telemetry arrow | `#999999` |
| Icon background | `#FFFFFF` |
| Diagram text | `#1A1A1A` |
| Secondary text | `#444444` |

---

## Layout Principles

These are guidelines for good output — adapt them to fit the diagram content:

- **Left to right** is the dominant flow direction. Users/internet on the left, private resources on the right, data stores furthest right.
- **Top to bottom** for tiers within a zone (ingress → app → data).
- **Generous whitespace** inside containers — icons should breathe, not crowd.
- **Align icons on a grid** — horizontally aligned icons look professional; scattered icons do not.
- **Numbered badges mark sequence** — add a badge at each step of a numbered flow. You do not need edge labels when badges are present.
- **Keep it readable at A4/letter size** — if you need to zoom in to read labels, the diagram is too dense. Reduce scope or split into two diagrams.
