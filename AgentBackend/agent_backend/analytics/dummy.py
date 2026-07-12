"""DummyStatsProvider — reasonable hardcoded outreach analytics for the director
briefing, until the BusinessLayer analytics endpoint is wired.

The numbers are internally consistent (the funnel narrows, per-counsellor sums
near the total) so the charts look realistic in a demo.
"""
from __future__ import annotations

from agent_backend.analytics.provider import Metric, OutreachStats, Point


class DummyStatsProvider:
    def get_outreach_stats(self, period: str | None = None) -> OutreachStats:
        return OutreachStats(
            period_label=period or "June 2026",
            headline_kpis=[
                Metric(label="Calls made", value=412, delta_pct=8.0),
                Metric(label="Reached", value=298, delta_pct=5.0),
                Metric(label="Interested", value=156, delta_pct=12.0),
                Metric(label="Applications started", value=63, delta_pct=-3.0),
                Metric(label="Avg call length (min)", value=6.4, delta_pct=2.0),
            ],
            calls_per_day=[
                Point(x="Jun 1", y=14), Point(x="Jun 2", y=18), Point(x="Jun 3", y=22),
                Point(x="Jun 4", y=9),  Point(x="Jun 5", y=27), Point(x="Jun 6", y=31),
                Point(x="Jun 7", y=12), Point(x="Jun 8", y=24), Point(x="Jun 9", y=29),
                Point(x="Jun 10", y=35),
            ],
            outcomes=[
                Point(x="Interested", y=156),
                Point(x="Follow-up later", y=92),
                Point(x="Not interested", y=50),
                Point(x="No answer", y=114),
            ],
            funnel=[
                Point(x="Called", y=412),
                Point(x="Reached", y=298),
                Point(x="Interested", y=156),
                Point(x="Applied", y=63),
                Point(x="Enrolled", y=21),
            ],
            per_counsellor=[
                Point(x="Aisha", y=132),
                Point(x="Rahul", y=98),
                Point(x="Meera", y=104),
                Point(x="Sana", y=78),
            ],
            per_programme=[
                Point(x="CSE AI&ML", y=58),
                Point(x="CSE Core", y=41),
                Point(x="ECE", y=24),
                Point(x="Business (BBA)", y=19),
                Point(x="Mechanical", y=14),
            ],
            language_split=[
                Point(x="English", y=210),
                Point(x="Telugu", y=128),
                Point(x="Hindi", y=58),
                Point(x="Tamil", y=16),
            ],
        )
