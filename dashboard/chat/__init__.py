"""VulnFlow chat subsystem.

Provides the right-side AI chat panel backend: structured envelope responses,
canvas mutation actions, sandboxed widget rendering, docs RAG grounding and
external tool integration. The implementation pattern (envelope shape,
widget renderer, skill-based progressive disclosure, plan/build/narrate
visualization workflow) is directly adapted from OpenGenerativeUI under
misc/OpenGenerativeUI.
"""

from dashboard.chat.routes import register_chat_routes

__all__ = ["register_chat_routes"]
