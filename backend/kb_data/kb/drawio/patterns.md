# Draw.io Pattern Library — Microsoft Reference Architecture

Copy-paste XML fragments for visual patterns used in Microsoft reference architecture diagrams. Each fragment shows the minimum structure — adapt IDs, coordinates, and labels to your diagram.

---

## 1. Numbered Flow Badge

Numbered green circles showing the sequence of a flow (①②③...). This is one of the most recognisable features of Microsoft reference diagrams.

```xml
<!-- Badge for step 1 — place near the first arrow or service icon -->
<mxCell id="badge-1" value="1"
  style="ellipse;aspect=fixed;fillColor=#107C10;fontColor=#FFFFFF;strokeColor=none;fontStyle=1;fontSize=11;align=center;verticalAlign=middle;html=1;"
  vertex="1" parent="1">
  <mxGeometry x="BADGE_X" y="BADGE_Y" width="26" height="26" as="geometry"/>
</mxCell>

<!-- Badge for step 2 -->
<mxCell id="badge-2" value="2"
  style="ellipse;aspect=fixed;fillColor=#107C10;fontColor=#FFFFFF;strokeColor=none;fontStyle=1;fontSize=11;align=center;verticalAlign=middle;html=1;"
  vertex="1" parent="1">
  <mxGeometry x="BADGE_X" y="BADGE_Y" width="26" height="26" as="geometry"/>
</mxCell>
```

**Positioning**: Place badges on the canvas (parent="1") at the midpoint of the arrow they annotate, or slightly to the upper-left of the target service icon. Use `parent="1"` even if the surrounding icons are inside containers — badges float above the diagram layer.

**Colour variant**: Use orange `#C55A11` instead of green when the flow is a response/return path rather than the request path.

**Sizing**: 26×26 is standard. Use 22×22 in dense diagrams where badges would crowd icons.

---

## 2. Diagram Title Block

Bold title at the top of the diagram, matching Microsoft reference style.

```xml
<!-- Title — place above all containers, parent="1" -->
<mxCell id="title" value="Your Architecture Title"
  style="text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;fontStyle=1;fontSize=16;fontColor=#1A1A1A;"
  vertex="1" parent="1">
  <mxGeometry x="30" y="20" width="700" height="36" as="geometry"/>
</mxCell>
```

Adjust width to match canvas width. Keep the y position at 20–30px above the first container.

---

## 3. VNet Container with Corner Icon

A Virtual Network box with the VNet icon anchored to the top-left, matching the Microsoft reference style.

```xml
<!-- VNet container -->
<mxCell id="vnet-main" value="Virtual network"
  style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;strokeWidth=2;dashed=1;dashPattern=8 4;fontStyle=1;fontSize=12;align=left;verticalAlign=top;spacingTop=8;spacingLeft=40;"
  vertex="1" parent="1">
  <mxGeometry x="VN_X" y="VN_Y" width="VN_W" height="VN_H" as="geometry"/>
</mxCell>

<!-- VNet icon pinned to top-left of the container — use small 28x28 -->
<mxCell id="vnet-icon" value=""
  style="sketch=0;outlineConnect=0;fontColor=#23272F;gradientColor=none;fillColor=#FFFFFF;strokeColor=none;dashed=0;html=1;shape=image;image=img/lib/azure2/networking/Virtual_Networks.svg;"
  vertex="1" parent="vnet-main">
  <mxGeometry x="6" y="6" width="28" height="28" as="geometry"/>
</mxCell>
```

The VNet icon is `parent="vnet-main"` so it stays anchored as you resize. Use `spacingLeft=40` in the container label to prevent the label text from overlapping the icon.

---

## 4. Subnet with NSG Corner Shield

A subnet box with a small NSG icon pinned to the top-right corner.

```xml
<!-- Subnet container — parent="vnet-main" to nest inside VNet -->
<mxCell id="snet-app" value="App subnet"
  style="rounded=0;whiteSpace=wrap;html=1;fillColor=#F0F7FF;strokeColor=#9BC2E6;strokeWidth=1;fontStyle=1;fontSize=11;align=left;verticalAlign=top;spacingTop=6;spacingLeft=6;"
  vertex="1" parent="vnet-main">
  <mxGeometry x="SNET_X" y="SNET_Y" width="SNET_W" height="SNET_H" as="geometry"/>
</mxCell>

<!-- NSG shield — top-right corner of the subnet. x = SNET_W - 36 -->
<mxCell id="nsg-app" value=""
  style="sketch=0;outlineConnect=0;fontColor=#23272F;gradientColor=none;fillColor=#FFFFFF;strokeColor=none;dashed=0;html=1;shape=image;image=img/lib/azure2/networking/Network_Security_Groups.svg;"
  vertex="1" parent="snet-app">
  <mxGeometry x="SNET_W_MINUS_36" y="4" width="30" height="30" as="geometry"/>
</mxCell>
```

Replace `SNET_W_MINUS_36` with `subnet_width - 36`. The NSG icon has no label — its position makes its role clear.

---

## 5. Private Endpoint Pattern

Private endpoints appear as dedicated icons connected by dashed lines. The pattern is: service → private endpoint icon → dashed arrow → backend service.

```xml
<!-- Private endpoint icon (inside private endpoint subnet or near the target service) -->
<mxCell id="pe-db" value="Private endpoint"
  style="sketch=0;outlineConnect=0;fontColor=#1A1A1A;gradientColor=none;fillColor=#FFFFFF;strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;shape=image;image=img/lib/azure2/networking/Private_Endpoint.svg;"
  vertex="1" parent="snet-data">
  <mxGeometry x="PE_X" y="PE_Y" width="48" height="48" as="geometry"/>
</mxCell>

<!-- Connection from caller to private endpoint — standard arrow -->
<mxCell id="e-to-pe" value="Private endpoint"
  style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#444444;strokeWidth=1.5;endArrow=block;endFill=1;"
  edge="1" parent="1" source="CALLER_ID" target="pe-db">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>

<!-- Connection from private endpoint to the actual service — dashed -->
<mxCell id="e-pe-to-svc" value=""
  style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#444444;strokeWidth=1;dashed=1;dashPattern=6 4;endArrow=block;endFill=1;"
  edge="1" parent="1" source="pe-db" target="SVC_ID">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

When a diagram has many private endpoints (one per PaaS service), line them up vertically in the private endpoint subnet for visual clarity.

---

## 6. Availability Zone Columns

Three-zone layout for zone-redundant deployments. Columns sit side by side inside the VNet or a subscription container.

```xml
<!-- Zone 1 column -->
<mxCell id="zone-1" value="Zone 1"
  style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FAFAFA;strokeColor=#CCCCCC;strokeWidth=1;fontStyle=1;fontSize=11;align=center;verticalAlign=top;spacingTop=6;fontColor=#555555;"
  vertex="1" parent="vnet-main">
  <mxGeometry x="Z1_X" y="Z1_Y" width="ZONE_W" height="ZONE_H" as="geometry"/>
</mxCell>

<!-- Zone 2 column — same width, offset by ZONE_W + 20 gap -->
<mxCell id="zone-2" value="Zone 2"
  style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FAFAFA;strokeColor=#CCCCCC;strokeWidth=1;fontStyle=1;fontSize=11;align=center;verticalAlign=top;spacingTop=6;fontColor=#555555;"
  vertex="1" parent="vnet-main">
  <mxGeometry x="Z2_X" y="Z1_Y" width="ZONE_W" height="ZONE_H" as="geometry"/>
</mxCell>

<!-- Zone 3 column -->
<mxCell id="zone-3" value="Zone 3"
  style="rounded=0;whiteSpace=wrap;html=1;fillColor=#FAFAFA;strokeColor=#CCCCCC;strokeWidth=1;fontStyle=1;fontSize=11;align=center;verticalAlign=top;spacingTop=6;fontColor=#555555;"
  vertex="1" parent="vnet-main">
  <mxGeometry x="Z3_X" y="Z1_Y" width="ZONE_W" height="ZONE_H" as="geometry"/>
</mxCell>
```

Replicate the services inside each zone column (e.g. App Service instance, database replica). Use the same vertical position within each column so services align horizontally across zones.

---

## 7. Linear Flow Diagram (no VNet containers)

For event-driven or pipeline diagrams (like Image Classification, API migration) that don't have network topology, use a simpler left-to-right layout with phase labels above service icons.

```xml
<!-- Phase label row — one per column of services, y position above icons -->
<mxCell id="phase-1" value="Event trigger"
  style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontStyle=1;fontSize=10;fontColor=#444444;"
  vertex="1" parent="1">
  <mxGeometry x="PHASE_X" y="PHASE_Y" width="120" height="20" as="geometry"/>
</mxCell>

<!-- Divider line below phase labels (optional, creates structure) -->
<mxCell id="divider" value=""
  style="endArrow=none;html=1;strokeColor=#DDDDDD;strokeWidth=1;"
  edge="1" parent="1">
  <mxGeometry x="START_X" y="DIVIDER_Y" width="END_X" height="0" relative="0" as="geometry">
    <mxPoint x="START_X" y="DIVIDER_Y" as="sourcePoint"/>
    <mxPoint x="END_X" y="DIVIDER_Y" as="targetPoint"/>
  </mxGeometry>
</mxCell>
```

Place service icons below the phase labels. Badges go between icons to mark the sequence. No container boxes needed.

---

## 8. Internet / Public Entry Point

Represents users accessing the system from the public internet.

```xml
<!-- Internet cloud / globe — always outside all containers, parent="1" -->
<mxCell id="internet" value="Public internet"
  style="sketch=0;outlineConnect=0;fontColor=#1A1A1A;gradientColor=none;fillColor=#FFFFFF;strokeColor=none;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;shape=image;image=img/lib/azure2/general/Globe.svg;"
  vertex="1" parent="1">
  <mxGeometry x="30" y="80" width="48" height="48" as="geometry"/>
</mxCell>
```

Place at the far left of the canvas. The first arrow leaves from this node to the entry service (Front Door, Application Gateway, etc.).

---

## 9. Managed Disk / Storage Attachment (VM Pattern)

When showing a VM with attached managed disks (OS disk + data disks), use a vertical stack of disk icons to the right of the VM.

```xml
<!-- OS Disk label -->
<mxCell id="disk-os-label" value="OS"
  style="text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;fontStyle=1;fontSize=10;fontColor=#444444;"
  vertex="1" parent="1">
  <mxGeometry x="LABEL_X" y="OS_Y" width="30" height="20" as="geometry"/>
</mxCell>

<!-- OS Disk icon -->
<mxCell id="disk-os" value=""
  style="sketch=0;outlineConnect=0;fontColor=#1A1A1A;gradientColor=none;fillColor=#FFFFFF;strokeColor=none;html=1;shape=image;image=img/lib/azure2/storage/Managed_Disks.svg;"
  vertex="1" parent="1">
  <mxGeometry x="DISK_X" y="OS_Y" width="40" height="40" as="geometry"/>
</mxCell>

<!-- Data Disk 1 -->
<mxCell id="disk-data-label" value="Data 1"
  style="text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;fontStyle=1;fontSize=10;fontColor=#444444;"
  vertex="1" parent="1">
  <mxGeometry x="LABEL_X" y="DATA1_Y" width="40" height="20" as="geometry"/>
</mxCell>
<mxCell id="disk-data-1" value=""
  style="sketch=0;outlineConnect=0;fontColor=#1A1A1A;gradientColor=none;fillColor=#FFFFFF;strokeColor=none;html=1;shape=image;image=img/lib/azure2/storage/Managed_Disks.svg;"
  vertex="1" parent="1">
  <mxGeometry x="DISK_X" y="DATA1_Y" width="40" height="40" as="geometry"/>
</mxCell>
```

Connect the VM to the disk stack with a horizontal line (no arrowhead): `endArrow=none;startArrow=none;`

---

## 10. On-Premises Zone (Migration Diagrams)

For diagrams showing hybrid connectivity or migration from on-premises.

```xml
<!-- On-premises zone container -->
<mxCell id="onprem" value="On-premises"
  style="rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#888888;strokeWidth=2;dashed=1;dashPattern=8 4;fontStyle=1;fontSize=13;align=left;verticalAlign=top;spacingTop=8;spacingLeft=8;"
  vertex="1" parent="1">
  <mxGeometry x="ONPREM_X" y="ONPREM_Y" width="ONPREM_W" height="ONPREM_H" as="geometry"/>
</mxCell>

<!-- Azure zone container (beside on-premises) -->
<mxCell id="azure-zone" value="Azure"
  style="rounded=0;whiteSpace=wrap;html=1;fillColor=#EFF6FC;strokeColor=#0078D4;strokeWidth=2;dashed=1;dashPattern=8 4;fontStyle=1;fontSize=13;align=left;verticalAlign=top;spacingTop=8;spacingLeft=8;"
  vertex="1" parent="1">
  <mxGeometry x="AZURE_X" y="ONPREM_Y" width="AZURE_W" height="ONPREM_H" as="geometry"/>
</mxCell>
```

Connect the two zones with arrows through the internet cloud (place a small internet icon between them). Use a lock icon label on the connecting arrow: `value="&#x1F512;"` or add a small Key Vault icon near the connection.

---

## Quick Cheat Sheet — Pattern vs. Use Case

| Pattern | When to use |
|---|---|
| Numbered badges | Any diagram with a defined flow sequence (most Microsoft reference diagrams) |
| Title block | Always — every diagram should have a title |
| VNet + corner icon | Any diagram with Azure networking / private resources |
| Subnet + NSG corner | Any subnet that has an NSG attached |
| Private endpoint | PaaS services (Key Vault, databases, ACR) accessed privately |
| AZ columns | Zone-redundant deployments (App Service, databases, VMs in multiple zones) |
| Linear flow / phase labels | Event-driven, pipeline, or migration diagrams without VNet topology |
| Managed disk stack | VM diagrams showing storage attachments |
| On-premises zone | Hybrid or migration architecture diagrams |
