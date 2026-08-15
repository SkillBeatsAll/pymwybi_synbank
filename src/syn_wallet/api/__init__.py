"""The dashboard's service and HTTP layer.

Reads the published tables, projects them into page payloads, and serves the
static front end. No financial logic lives here or downstream of here.
"""

from __future__ import annotations

from . import service

__all__ = ["service"]
