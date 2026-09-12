"""Local MCP connection settings and lifecycle; no implicit report authorization."""
from .config import ConnectorError
from .service import ConnectorService

__all__ = ['ConnectorError', 'ConnectorService']
