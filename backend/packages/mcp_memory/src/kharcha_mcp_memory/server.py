"""``kharcha-mcp-memory``: memory tools as an MCP server (streamable HTTP, port 8103)."""

from kharcha_common.settings import Settings, get_settings
from kharcha_mcp_kit.serve import serve
from kharcha_mcp_memory import tools
from kharcha_mcp_memory.embed import HashEmbedder, OllamaEmbedder

PORT = 8103


def configure_embedder(settings: Settings) -> None:
    tools.EMBEDDER = (
        OllamaEmbedder(settings.ollama_api_base, settings.embedding_model)
        if settings.embedding_backend == "ollama"
        else HashEmbedder()
    )


def run() -> None:
    configure_embedder(get_settings())
    serve("kharcha-mcp-memory", tools.TOOLS, PORT)
