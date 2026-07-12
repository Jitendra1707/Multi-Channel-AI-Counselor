"""Outreach analytics for the director-briefing presenter.

`get_stats_provider()` returns the active `StatsProvider` (dummy now,
BusinessLayer later by config). The director's `present_analytics` tool calls it
to fetch aggregated outreach figures; the LLM then decides which visual to show.
"""
from agent_backend.analytics.provider import StatsProvider, get_stats_provider

__all__ = ["StatsProvider", "get_stats_provider"]
