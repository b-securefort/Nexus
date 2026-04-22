"""
Network connectivity test tool — DNS lookups, port checks, NSG rule queries.
Read-only, no approval needed.
"""

import json
import logging
import socket
import subprocess
import sys

from app.auth.models import User
from app.tools.base import SUBPROCESS_FLAGS, Tool
from app.tools.az_login_check import require_az_login
from app.tools.az_cli import _find_az

logger = logging.getLogger(__name__)

_MAX_OUTPUT_SIZE = 8192

# Only allow testing well-known ports and Azure service ports
_ALLOWED_PORTS = set(range(1, 65536))


class NetworkTestTool(Tool):
    name = "network_test"
    description = (
        "Test network connectivity: DNS resolution, TCP port checks, "
        "and Azure NSG rule queries. Read-only — no approval needed. "
        "Use this to diagnose connectivity issues between Azure resources."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["dns_lookup", "port_check", "nsg_rules"],
                "description": (
                    "Action to perform:\n"
                    "- dns_lookup: Resolve a hostname to IP address(es)\n"
                    "- port_check: Test TCP connectivity to host:port\n"
                    "- nsg_rules: List effective NSG rules for a NIC or subnet"
                ),
            },
            "hostname": {
                "type": "string",
                "description": "Hostname for dns_lookup or port_check.",
            },
            "port": {
                "type": "integer",
                "description": "TCP port for port_check. Default: 443.",
            },
            "resource_group": {
                "type": "string",
                "description": "Resource group for nsg_rules.",
            },
            "nsg_name": {
                "type": "string",
                "description": "NSG name for nsg_rules.",
            },
        },
        "required": ["action"],
    }
    requires_approval = False

    def execute(self, args: dict, user: User) -> str:
        action = args.get("action", "")

        if action == "dns_lookup":
            return self._dns_lookup(args.get("hostname", ""))
        elif action == "port_check":
            return self._port_check(args.get("hostname", ""), args.get("port", 443))
        elif action == "nsg_rules":
            login_err = require_az_login()
            if login_err:
                return login_err
            return self._nsg_rules(args.get("resource_group", ""), args.get("nsg_name", ""))
        else:
            return f"Error: Unknown action '{action}'"

    def _dns_lookup(self, hostname: str) -> str:
        if not hostname:
            return "Error: hostname is required for dns_lookup"
        # Basic validation
        if not all(c.isalnum() or c in ".-_" for c in hostname):
            return "Error: Invalid hostname"
        try:
            results = socket.getaddrinfo(hostname, None)
            ips = sorted(set(r[4][0] for r in results))
            return f"DNS resolution for {hostname}:\n" + "\n".join(f"  - {ip}" for ip in ips)
        except socket.gaierror as e:
            return f"DNS resolution failed for {hostname}: {e}"
        except Exception as e:
            return f"Error: {e}"

    def _port_check(self, hostname: str, port: int) -> str:
        if not hostname:
            return "Error: hostname is required for port_check"
        if port not in _ALLOWED_PORTS:
            return f"Error: Port {port} is out of range (1-65535)"
        if not all(c.isalnum() or c in ".-_" for c in hostname):
            return "Error: Invalid hostname"
        try:
            sock = socket.create_connection((hostname, port), timeout=10)
            sock.close()
            return f"TCP connection to {hostname}:{port} — SUCCESS (open)"
        except socket.timeout:
            return f"TCP connection to {hostname}:{port} — TIMEOUT (filtered/blocked)"
        except ConnectionRefusedError:
            return f"TCP connection to {hostname}:{port} — REFUSED (closed)"
        except socket.gaierror as e:
            return f"TCP connection to {hostname}:{port} — DNS FAILED: {e}"
        except Exception as e:
            return f"TCP connection to {hostname}:{port} — ERROR: {e}"

    def _nsg_rules(self, resource_group: str, nsg_name: str) -> str:
        if not resource_group or not nsg_name:
            return "Error: resource_group and nsg_name are required for nsg_rules"
        try:
            cmd = [
                _find_az(), "network", "nsg", "rule", "list",
                "--resource-group", resource_group,
                "--nsg-name", nsg_name,
                "--output", "json",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                shell=(sys.platform == "win32"), **SUBPROCESS_FLAGS,
            )
            if result.returncode != 0:
                return f"Error: {result.stderr.strip() if result.stderr else 'Unknown error'}"
            output = result.stdout.strip()
            if len(output) > _MAX_OUTPUT_SIZE:
                output = output[:_MAX_OUTPUT_SIZE] + "\n... (truncated)"
            return output if output else "No NSG rules found."
        except subprocess.TimeoutExpired:
            return "Error: NSG query timed out"
        except Exception as e:
            return f"Error: {e}"
