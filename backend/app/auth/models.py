"""User model for authentication."""

from dataclasses import dataclass, field


@dataclass
class User:
    """Represents an authenticated user extracted from Entra ID token."""

    oid: str
    email: str
    display_name: str
    # ARM token acquired by the frontend via MSAL for https://management.azure.com/.
    # Present when the user has consented to the ARM scope; None in dev-bypass mode
    # or when the frontend couldn't acquire the token (e.g. no ARM consent yet).
    # Azure tools inject this as AZURE_ACCESS_TOKEN so commands run as the user,
    # not as the server's managed identity / service principal.
    arm_token: str | None = field(default=None)
