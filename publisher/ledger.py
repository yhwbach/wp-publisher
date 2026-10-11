"""Postgres ledger of what was pushed per client. The source of truth for idempotency across runs."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import TIMESTAMP
from sqlmodel import TEXT, Column, Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

LedgerStatus = Literal["pending", "posted", "failed"]


class PublishLedger(SQLModel, table=True):
    __tablename__ = "publish_ledger"

    client_id: str = Field(primary_key=True)
    slug: str = Field(primary_key=True)
    content_hash: str
    status: str = Field(index=True)
    wp_post_id: int | None = None
    last_error: str | None = Field(default=None, sa_column=Column(TEXT, nullable=True))
    run_id: UUID
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(TIMESTAMP(timezone=True), nullable=False),
    )


class PublishLedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, client_id: str, slug: str) -> PublishLedger | None:
        return await self.session.get(PublishLedger, (client_id, slug))

    async def record(
        self,
        client_id: str,
        slug: str,
        content_hash: str,
        run_id: UUID,
        status: LedgerStatus,
        wp_post_id: int | None = None,
        last_error: str | None = None,
    ) -> PublishLedger:
        # Committed per row so a crash mid-batch leaves an accurate ledger.
        entry = await self.session.merge(
            PublishLedger(
                client_id=client_id,
                slug=slug,
                content_hash=content_hash,
                run_id=run_id,
                status=status,
                wp_post_id=wp_post_id,
                last_error=last_error,
                updated_at=datetime.now(UTC),
            )
        )
        await self.session.commit()
        return entry
