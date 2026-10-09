"""Errors shared by every subdomain."""

from __future__ import annotations


class DomainError(ValueError):
    """A business rule refused the request; the message is shown to the user as is."""
