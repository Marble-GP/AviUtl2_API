"""MCP server integration for the AviUtl2 Live Bridge (Phase 1).

Importing this package never requires the optional ``mcp`` dependency;
only :mod:`aviutl2_api.mcp.server` pulls in FastMCP.
"""

from .core import (
    AGENT_CONFIRMATION_METHODS,
    METHOD_DESCRIPTIONS,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    WIRE_CONFIRMATION_METHODS,
    build_card,
    check_confirmation,
    error_payload,
    grouped_methods,
    is_known_method,
    method_category,
    method_not_found,
    method_unavailable,
    strip_confirmation,
    summarize_status,
)

__all__ = [
    "AGENT_CONFIRMATION_METHODS",
    "METHOD_DESCRIPTIONS",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "WIRE_CONFIRMATION_METHODS",
    "build_card",
    "check_confirmation",
    "error_payload",
    "grouped_methods",
    "is_known_method",
    "method_category",
    "method_not_found",
    "method_unavailable",
    "strip_confirmation",
    "summarize_status",
]
