"""Cross-cutting infrastructure: logging, future telemetry, error types.

Imports go through `infra/` so the backend implementation can swap
later (structlog → OTel, etc.) without touching feature code.
"""

from agent_backend.infra.logger import configure_logging, get_logger
from agent_backend.infra.tracing import trace_config, warmup_tracing

__all__ = ["configure_logging", "get_logger", "trace_config", "warmup_tracing"]
