"""Channel adapters — one subpackage per channel (pipecat, chat, email, ...).

Each channel is self-contained: it owns its transport, its
processors, and any channel-specific services. Channels MUST NOT
import from each other; the only shared dependency is `llm_agent`.

Phase 1: only `pipecat` is implemented. Subsequent phases add more
channels following the same shape.
"""
