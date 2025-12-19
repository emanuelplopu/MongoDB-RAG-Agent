"""Tools package for AI agent capabilities.

This package provides tools that the LLM agent can use to perform actions
beyond simple text generation, such as web browsing, code execution, etc.
"""

from backend.tools.browser_tool import BrowserTool, browse_url, BROWSER_TOOL_SCHEMA

__all__ = [
    "BrowserTool",
    "browse_url",
    "BROWSER_TOOL_SCHEMA",
]
