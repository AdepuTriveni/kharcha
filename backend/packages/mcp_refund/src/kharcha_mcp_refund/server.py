"""``kharcha-mcp-refund``: the refund tools as an MCP server (streamable HTTP, port 8104)."""

from kharcha_mcp_kit.serve import serve
from kharcha_mcp_refund.tools import TOOLS

PORT = 8104


def run() -> None:
    serve("kharcha-mcp-refund", TOOLS, PORT)
