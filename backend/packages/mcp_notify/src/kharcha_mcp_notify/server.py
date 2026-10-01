"""``kharcha-mcp-notify``: the notify tools as an MCP server (streamable HTTP, port 8102)."""

from kharcha_mcp_kit.serve import serve
from kharcha_mcp_notify.tools import TOOLS

PORT = 8102


def run() -> None:
    serve("kharcha-mcp-notify", TOOLS, PORT)
