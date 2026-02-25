"""AgentiBridge — Claude CLI transcript index and MCP tools."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentibridge")
except PackageNotFoundError:
    __version__ = "dev"
