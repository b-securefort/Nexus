"""A complex AWS architecture authored from scratch (no source image) to stress
the engine: deep nesting (VPC ▸ tier ▸ AZ-subnet ▸ resource), ~14 nodes, two AZs,
edge tier, side services, and a cross-AZ replication edge. Proves the SAME engine
+ a swapped icon catalog handles AWS, not just Azure.

Production multi-AZ 3-tier web app:
  Users → Route53 → CloudFront(+WAF) → ALB → ECS (2 AZs) → Aurora writer/reader,
  with NAT egress per AZ and S3 / Secrets Manager / DynamoDB as VPC-adjacent services.
"""

from app.diagram_ir.schema import Adornment, Container, Diagram, Edge, Node


def build() -> Diagram:
    containers = [
        Container(id="canvas", label="", style="band", layout="column",
                  children=["top_band", "main_band"]),
        Container(id="top_band", label="", style="band", layout="row", parent="canvas",
                  children=["s3", "secrets", "ddb"]),
        Container(id="main_band", label="", style="band", layout="row", parent="canvas",
                  children=["users", "edge", "vpc"]),
        Container(id="edge", label="", style="band", layout="column", parent="main_band",
                  children=["route53", "cloudfront"]),

        Container(id="vpc", label="VPC  10.0.0.0/16", style="vpc", layout="column",
                  parent="main_band", children=["alb", "public_tier", "app_tier", "data_tier"],
                  adornments=[Adornment(icon="aws/vpc", corner="top-left")]),

        Container(id="public_tier", label="", style="band", layout="row", parent="vpc",
                  children=["pub_a", "pub_b"]),
        Container(id="pub_a", label="Public subnet  us-east-1a", style="subnet", layout="row",
                  parent="public_tier", children=["nat_a"]),
        Container(id="pub_b", label="Public subnet  us-east-1b", style="subnet", layout="row",
                  parent="public_tier", children=["nat_b"]),

        Container(id="app_tier", label="", style="band", layout="row", parent="vpc",
                  children=["app_a", "app_b"]),
        Container(id="app_a", label="App subnet  us-east-1a", style="subnet", layout="row",
                  parent="app_tier", children=["ecs_a"]),
        Container(id="app_b", label="App subnet  us-east-1b", style="subnet", layout="row",
                  parent="app_tier", children=["ecs_b"]),

        Container(id="data_tier", label="", style="band", layout="row", parent="vpc",
                  children=["data_a", "data_b"]),
        Container(id="data_a", label="Data subnet  us-east-1a", style="subnet", layout="row",
                  parent="data_tier", children=["aurora_w"]),
        Container(id="data_b", label="Data subnet  us-east-1b", style="subnet", layout="row",
                  parent="data_tier", children=["aurora_r"]),
    ]

    nodes = [
        Node(id="users", label="Users", icon="shape/actor", parent="main_band"),
        Node(id="route53", label="Route 53", icon="aws/route_53", parent="edge"),
        Node(id="cloudfront", label="CloudFront", icon="aws/cloudfront", parent="edge",
             adornments=[Adornment(icon="aws/waf", corner="top-right", label="WAF")]),
        Node(id="alb", label="Application Load Balancer", icon="aws/application_load_balancer", parent="vpc"),
        Node(id="nat_a", label="NAT gateway", icon="aws/nat_gateway", parent="pub_a"),
        Node(id="nat_b", label="NAT gateway", icon="aws/nat_gateway", parent="pub_b"),
        Node(id="ecs_a", label="ECS service", icon="aws/ecs", parent="app_a"),
        Node(id="ecs_b", label="ECS service", icon="aws/ecs", parent="app_b"),
        Node(id="aurora_w", label="Aurora (writer)", icon="aws/aurora", parent="data_a"),
        Node(id="aurora_r", label="Aurora (reader)", icon="aws/aurora", parent="data_b"),
        Node(id="s3", label="S3 (assets)", icon="aws/s3", parent="top_band"),
        Node(id="secrets", label="Secrets Manager", icon="aws/secrets_manager", parent="top_band"),
        Node(id="ddb", label="DynamoDB (sessions)", icon="aws/dynamodb", parent="top_band"),
    ]

    edges = [
        Edge("users", "route53", "flow"),
        Edge("route53", "cloudfront", "flow"),
        Edge("cloudfront", "alb", "flow", "HTTPS"),
        Edge("alb", "ecs_a", "flow"),
        Edge("alb", "ecs_b", "flow"),
        Edge("ecs_a", "aurora_w", "flow", "writes"),
        Edge("ecs_b", "aurora_w", "flow"),
        Edge("aurora_w", "aurora_r", "replication", "Multi-AZ replication"),
        Edge("ecs_a", "nat_a", "flow", "egress"),
        Edge("ecs_b", "nat_b", "flow"),
        Edge("ecs_a", "s3", "private", "assets"),
        Edge("ecs_a", "secrets", "private", "secrets"),
        Edge("ecs_a", "ddb", "private", "sessions"),
    ]

    return Diagram(title="AWS multi-AZ 3-tier web app", direction="LR",
                   containers=containers, nodes=nodes, edges=edges)
