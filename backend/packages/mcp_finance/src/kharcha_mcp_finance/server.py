"""``kharcha-mcp-finance``: the finance tools as an MCP server (streamable HTTP, port 8101)."""

from kharcha_mcp_finance.tools import TOOLS
from kharcha_mcp_kit.serve import serve

PORT = 8101


def run() -> None:
    serve("kharcha-mcp-finance", TOOLS, PORT)
