"""JsonStatsProvider — outreach analytics read from a per-month JSON file.

The director-briefing presenter's `present_analytics` tool depends only on the
`StatsProvider` Protocol (provider.py), so pointing it at real per-month figures
is a config swap (ANALYTICS_PROVIDER=json), not an agent change.

File shape (test-data/outreach_stats.json):

    {
      "default_period": "july",
      "year": 2026,
      "months": [
        {"key": "june", "label": "June 2026",
         "total_leads": 480, "outreach_calls": 412, "interested": 156,
         "not_interested": 98, "escalated_to_human_counsellor": 41},
        {"key": "july", "label": "July 2026", ...}
      ]
    }

Each month carries exactly five raw metrics. The provider maps them into the
existing `OutreachStats` shape: the five numbers become `headline_kpis` (with a
delta vs the prior month in the list), plus a derived `outcomes` donut and
`funnel` bar. The per-day / per-counsellor / language slices stay empty — the
viz LLM only uses what's populated.

The file is re-read on every call (it's tiny), so editing the numbers is picked
up without a server restart. Fail-soft: a missing/malformed file logs a warning
and falls back to the hardcoded DummyStatsProvider so a live call never crashes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_backend.analytics.provider import Metric, OutreachStats, Point
from agent_backend.infra import get_logger

log = get_logger(__name__)

# Canonical metric order + human labels. Shared as the single source of truth so
# the provider (KPI strip) and the present_analytics tool (spoken figures) agree
# on names. The key is the JSON field; the label is what's said/shown.
METRIC_FIELDS: list[tuple[str, str]] = [
    ("total_leads", "Total leads"),
    ("outreach_calls", "Outreach calls"),
    ("interested", "Interested"),
    ("not_interested", "Not interested"),
    ("escalated_to_human_counsellor", "Escalated to counsellor"),
]


class JsonStatsProvider:
    def __init__(self, path: str) -> None:
        self._path = Path(path)

    # --- file access (fail-soft) -------------------------------------------
    def _load(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("[analytics] stats file not found — falling back to dummy", path=str(self._path))
            return None
        except Exception as e:  # noqa: BLE001 — any parse/IO error is fail-soft
            log.warning("[analytics] stats file unreadable — falling back to dummy",
                        path=str(self._path), err=str(e)[:200])
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("months"), list) or not raw["months"]:
            log.warning("[analytics] stats file has no 'months' — falling back to dummy", path=str(self._path))
            return None
        return raw

    def _months(self) -> list[dict[str, Any]]:
        data = self._load()
        if data is None:
            return []
        return [m for m in data["months"] if isinstance(m, dict)]

    # --- public helpers used by present_analytics --------------------------
    def available_periods(self) -> list[dict[str, str]]:
        """[{key, label}, ...] for every month present — lets the tool detect
        which month(s) a question refers to. Empty list if the file is unusable."""
        out: list[dict[str, str]] = []
        for m in self._months():
            key = str(m.get("key", "")).strip()
            label = str(m.get("label", key)).strip()
            if key:
                out.append({"key": key, "label": label})
        return out

    def _resolve_index(self, months: list[dict[str, Any]], period: str | None) -> int:
        """Index of the month matching `period` (substring match on key/label),
        else the configured default, else the last (latest) month."""
        if period:
            needle = period.strip().lower()
            for i, m in enumerate(months):
                key = str(m.get("key", "")).lower()
                label = str(m.get("label", "")).lower()
                if needle and (needle in key or key in needle or needle in label):
                    return i
        # default_period from the file, else latest
        data = self._load() or {}
        default = str(data.get("default_period", "")).strip().lower()
        if default:
            for i, m in enumerate(months):
                if default in str(m.get("key", "")).lower():
                    return i
        return len(months) - 1

    def get_month(self, period: str | None = None) -> dict[str, Any] | None:
        """Raw month record (key, label, the five metrics) for `period`, or the
        default/latest. None if no usable data — callers then fall back."""
        months = self._months()
        if not months:
            return None
        return months[self._resolve_index(months, period)]

    # --- StatsProvider protocol --------------------------------------------
    def get_outreach_stats(self, period: str | None = None) -> OutreachStats:
        months = self._months()
        if not months:
            from agent_backend.analytics.dummy import DummyStatsProvider

            return DummyStatsProvider().get_outreach_stats(period)

        idx = self._resolve_index(months, period)
        month = months[idx]
        prior = months[idx - 1] if idx > 0 else None

        def _num(rec: dict[str, Any], field: str) -> float:
            try:
                return float(rec.get(field, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        def _delta_pct(field: str) -> float | None:
            if prior is None:
                return None
            prev = _num(prior, field)
            if prev == 0:
                return None
            return round((_num(month, field) - prev) / prev * 100, 1)

        kpis = [
            Metric(label=label, value=_num(month, field), delta_pct=_delta_pct(field))
            for field, label in METRIC_FIELDS
        ]

        interested = _num(month, "interested")
        not_interested = _num(month, "not_interested")
        escalated = _num(month, "escalated_to_human_counsellor")
        total_leads = _num(month, "total_leads")
        outreach_calls = _num(month, "outreach_calls")

        return OutreachStats(
            period_label=str(month.get("label") or month.get("key") or "This period"),
            headline_kpis=kpis,
            outcomes=[
                Point(x="Interested", y=interested),
                Point(x="Not interested", y=not_interested),
                Point(x="Escalated", y=escalated),
            ],
            funnel=[
                Point(x="Total leads", y=total_leads),
                Point(x="Outreach calls", y=outreach_calls),
                Point(x="Interested", y=interested),
                Point(x="Escalated", y=escalated),
            ],
        )
