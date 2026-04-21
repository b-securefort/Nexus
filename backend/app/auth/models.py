"""User model for authentication."""

from dataclasses import dataclass


@dataclass
class User:
    """Represents an authenticated user extracted from Entra ID token."""

    oid: str
    email: str
    display_name: str
