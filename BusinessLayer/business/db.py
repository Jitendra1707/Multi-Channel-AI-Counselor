"""Async DB engine + schema init — PostgreSQL.

The BusinessLayer persists to PostgreSQL via SQLAlchemy async (the `asyncpg`
driver). `DATABASE_URL` is the only knob — a Postgres DSN of the form
`postgresql+asyncpg://user:pass@host:5432/dbname`; in Kubernetes inject it as an
env var / secret. The engine and session factory are process-wide singletons.

Schema is created from the SQLModel models on boot (`create_all`, idempotent).
Lead DATA is loaded manually via `sql/seed_leads.sql` — the app never seeds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from business.config import get_settings
from business.logging import get_logger

log = get_logger(__name__)

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is not None:
        return _engine

    s = get_settings()
    # pool_pre_ping recycles connections that the DB / a k8s network blip dropped
    # (avoids "server closed the connection unexpectedly" after idle); pool_recycle
    # proactively rotates connections before proxy/DB idle timeouts close them.
    _engine = create_async_engine(
        s.database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transactional session. Commits on success, rolls back on error."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Additive, idempotent column migrations applied AFTER create_all. SQLModel's
# create_all creates missing TABLES but never alters an existing one, so a column
# added to a model after the table already exists in Postgres won't appear. Each
# entry here is an `ADD COLUMN IF NOT EXISTS` that's safe to run on every boot —
# the standard lightweight path for additive schema changes without Alembic.
_COLUMN_MIGRATIONS: tuple[str, ...] = (
    # is_lead: raw data (False) vs real lead (True). Backfill existing rows to
    # True so every pre-existing lead keeps behaving as a real, dialable lead.
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS is_lead BOOLEAN NOT NULL DEFAULT TRUE",
    # funnel_stage: admissions lifecycle. May already exist (added by an earlier
    # deploy) — IF NOT EXISTS makes this a no-op then. Default 'lead' so new rows
    # are real leads; backfill any NULLs (pre-existing rows) to 'lead'.
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS funnel_stage VARCHAR NOT NULL DEFAULT 'lead'",
    "UPDATE leads SET funnel_stage = 'lead' WHERE funnel_stage IS NULL",
    # Keep is_lead consistent with funnel_stage for any pre-existing rows: raw
    # stage ⇒ not a lead; everything else ⇒ a lead.
    "UPDATE leads SET is_lead = (funnel_stage <> 'raw')",
    # ------------------------------------------------------------------ #
    # lead_priority: lead temperature (hot/warm/cold), split OUT of `status`.
    # New column + one-time migration off the legacy tier/application statuses.
    # ------------------------------------------------------------------ #
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_priority VARCHAR DEFAULT NULL",
    # Backfill temperature from the existing interest score (new 80/50 bands).
    # Only touch rows not yet scored (NULL) and with a real interest signal;
    # interest=0 (never analyzed) stays NULL.
    "UPDATE leads SET lead_priority = CASE "
    "WHEN interest >= 80 THEN 'hot' WHEN interest >= 50 THEN 'warm' ELSE 'cold' END "
    "WHERE lead_priority IS NULL AND interest > 0",
    # Preserve a legacy status='hot' as priority even if its interest was low.
    "UPDATE leads SET lead_priority = 'hot' WHERE status = 'hot'",
    # The admissions lifecycle now lives ONLY in funnel_stage. Lift any legacy
    # application_* that was parked in `status` into funnel_stage (forward-only —
    # never regress a row already at an equal/later stage).
    "UPDATE leads SET funnel_stage = 'application_started' "
    "WHERE status = 'application_started' "
    "AND funnel_stage NOT IN ('application_started','fees_pending','application_submitted')",
    "UPDATE leads SET funnel_stage = 'fees_pending' "
    "WHERE status = 'application_completed_payment_pending' "
    "AND funnel_stage NOT IN ('fees_pending','application_submitted')",
    "UPDATE leads SET funnel_stage = 'application_submitted' "
    "WHERE status = 'application_submitted' AND funnel_stage <> 'application_submitted'",
    # Collapse the retired status values onto operational equivalents:
    #   hot → delegated (a human owns a hot lead, matching the escalation path)
    #   cold/warm/application_* → called (engaged before → dialable, opens as
    #   a returning candidate; lifecycle now carried by funnel_stage above).
    "UPDATE leads SET status = 'delegated' WHERE status = 'hot'",
    "UPDATE leads SET status = 'called' WHERE status IN "
    "('cold','warm','application_started','application_completed_payment_pending','application_submitted')",
    # Re-sync is_lead after the funnel_stage lifts above.
    "UPDATE leads SET is_lead = (funnel_stage <> 'raw')",
)


async def init_db() -> None:
    """Create tables if absent (idempotent — safe on every boot), then apply any
    additive column migrations. Lead DATA is loaded separately via the manual SQL
    seed script; the app never seeds."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        for ddl in _COLUMN_MIGRATIONS:
            await conn.execute(text(ddl))
    log.info("db initialised", url=_redact_url(get_settings().database_url))


def _redact_url(url: str) -> str:
    if "@" not in url:
        return url
    # postgresql+asyncpg://user:pass@host/db -> postgresql+asyncpg://***@host/db
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"
