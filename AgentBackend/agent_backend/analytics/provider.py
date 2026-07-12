"""StatsProvider — the outreach analytics source for the director presenter.

A Protocol with swap-by-config implementations (mirrors the telephony-provider
pattern): `DummyStatsProvider` now, `BusinessLayerStatsProvider` later. The
director's `present_analytics` tool depends only on this interface, so wiring
real data is a config change, not an agent change.

IMPORTANT: aggregation is deterministic Python here — the LLM never computes the
numbers. The provider returns a structured `OutreachStats`; the LLM only decides
how to VISUALISE the relevant slice.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


# --- The data shape the provider returns -----------------------------------
class Metric(BaseModel):
    label: str
    value: float
    delta_pct: float | None = None   # change vs prior period, percent


class Point(BaseModel):
    x: str
    y: float


class OutreachStats(BaseModel):
    """One period's outreach analytics. All figures pre-aggregated.

    Only `period_label` + `headline_kpis` are required; the breakdown lists
    default to empty so a provider can fill just the slices it actually has
    (e.g. the JSON provider has the 5 headline metrics + derived outcomes/funnel,
    but no per-day / per-counsellor / language detail). The viz LLM simply uses
    whatever slices are populated."""
    period_label: str = Field(description="e.g. 'June 2026'.")
    headline_kpis: list[Metric]                       # calls, reached, converted, etc.
    calls_per_day: list[Point] = Field(default_factory=list)    # date -> calls
    outcomes: list[Point] = Field(default_factory=list)         # outcome -> count
    funnel: list[Point] = Field(default_factory=list)           # stage -> count
    per_counsellor: list[Point] = Field(default_factory=list)   # counsellor -> calls
    per_programme: list[Point] = Field(default_factory=list)     # programme -> interested leads
    language_split: list[Point] = Field(default_factory=list)   # language -> calls


@runtime_checkable
class StatsProvider(Protocol):
    def get_outreach_stats(self, period: str | None = None) -> OutreachStats: ...


@lru_cache(maxsize=1)
def get_stats_provider() -> StatsProvider:
    """Active provider, chosen by config (ANALYTICS_PROVIDER).

      - "json"  → JsonStatsProvider over ANALYTICS_STATS_FILE (per-month figures).
      - "dummy" → the hardcoded DummyStatsProvider (legacy demo numbers).

    One instance per process (lru_cache). The JSON provider re-reads its file on
    every call, so editing the stats file is picked up without a restart even
    though the provider object itself is cached here.
    """
    from agent_backend.config import get_settings

    s = get_settings()
    if s.analytics_provider == "json":
        from agent_backend.analytics.json_provider import JsonStatsProvider

        return JsonStatsProvider(s.analytics_stats_file)

    from agent_backend.analytics.dummy import DummyStatsProvider

    return DummyStatsProvider()
