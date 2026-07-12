"""UiDirective — the contract the director-briefing agent emits to the FE.

The agent never sends raw chart-library config or code. It emits a CONSTRAINED,
validated `UiDirective`: a list of typed blocks (KPI strip, bar/line chart,
donut, table, report). The FE maps each block `kind` → a prebuilt Recharts
component via a registry. This makes the surface safe (validated both ends) and
extensible (new chart = one block variant here + one component on the FE).

The same schema is used two ways:
  1. As the JSON-schema for OpenAI structured outputs, so the viz LLM CANNOT
     emit malformed JSON.
  2. As the validator before the directive is pushed over the data channel.

Keep this in sync with the FE mirror at web-app/src/lib/uiDirective.ts.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


# --- Block primitives ------------------------------------------------------
class KpiItem(BaseModel):
    label: str = Field(description="Short metric name, e.g. 'Calls made'.")
    value: str = Field(description="Display value as a string, e.g. '412' or '38%'.")
    delta: str | None = Field(
        default=None,
        description="Optional change vs prior period, e.g. '+8%' or '-3%'. Omit if N/A.",
    )
    trend: Literal["up", "down", "flat"] | None = Field(
        default=None, description="Direction of delta, for colour/arrow on the FE."
    )


class SeriesPoint(BaseModel):
    """One (category, value) pair for bar/line/donut series."""
    x: str = Field(description="Category / x-axis label, e.g. a date or a name.")
    y: float = Field(description="Numeric value for this point.")


# --- Blocks (discriminated union on `kind`) --------------------------------
class KpiBlock(BaseModel):
    kind: Literal["kpi"] = "kpi"
    title: str | None = None
    items: list[KpiItem] = Field(description="The metric cards in this strip.")


class BarBlock(BaseModel):
    kind: Literal["bar"] = "bar"
    title: str
    x_label: str | None = None
    y_label: str | None = None
    series: list[SeriesPoint]


class LineBlock(BaseModel):
    kind: Literal["line"] = "line"
    title: str
    x_label: str | None = None
    y_label: str | None = None
    series: list[SeriesPoint]


class DonutBlock(BaseModel):
    kind: Literal["donut"] = "donut"
    title: str
    series: list[SeriesPoint] = Field(description="Each slice as (label=x, value=y).")


class TableBlock(BaseModel):
    kind: Literal["table"] = "table"
    title: str
    columns: list[str]
    rows: list[list[str]] = Field(description="Row-major cells, each a string.")


class ReportBlock(BaseModel):
    kind: Literal["report"] = "report"
    title: str
    body: str = Field(description="Short prose summary the director can read.")


Block = Annotated[
    Union[KpiBlock, BarBlock, LineBlock, DonutBlock, TableBlock, ReportBlock],
    Field(discriminator="kind"),
]


# --- The directive ---------------------------------------------------------
class UiDirective(BaseModel):
    """One screen update for the director's panel. Pushed over the data channel
    as {"type": "ui_directive", "directive": <this>}."""

    version: int = Field(default=SCHEMA_VERSION)
    title: str = Field(description="Panel title, e.g. 'Outreach — June 2026'.")
    blocks: list[Block] = Field(
        description="Ordered visual blocks; the FE renders them top-to-bottom.",
        min_length=1,
    )
    narration: str | None = Field(
        default=None,
        description="Optional one-line caption the FE may show; NOT the spoken line.",
    )


__all__ = [
    "SCHEMA_VERSION",
    "UiDirective",
    "KpiBlock", "BarBlock", "LineBlock", "DonutBlock", "TableBlock", "ReportBlock",
    "KpiItem", "SeriesPoint",
]
