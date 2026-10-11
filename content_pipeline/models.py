from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import TIMESTAMP, Index
from sqlmodel import TEXT, Column, Field, SQLModel

EMBEDDING_DIMS = 1536


def _now() -> datetime:
    return datetime.now(UTC)


class ClientPage(SQLModel, table=True):
    """A live page on a client's site, refreshed by the weekly crawl. The internal-link pool."""

    __tablename__ = "client_pages"
    __table_args__ = (
        Index(
            "ix_client_pages_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    client_id: str = Field(primary_key=True)
    url: str = Field(primary_key=True)
    title: str = Field(sa_column=Column(TEXT, nullable=False))
    summary: str = Field(sa_column=Column(TEXT, nullable=False))
    page_type: str = "post"
    is_money_page: bool = False
    http_status: int
    indexable: bool
    embedding: list[float] = Field(sa_column=Column(Vector(EMBEDDING_DIMS), nullable=False))
    crawled_at: datetime = Field(default_factory=_now, sa_column=Column(TIMESTAMP(timezone=True), nullable=False))


class ClientStyleCorpus(SQLModel, table=True):
    """Approved posts per client, the reference set for tone-drift checks."""

    __tablename__ = "client_style_corpus"

    client_id: str = Field(primary_key=True)
    post_url: str = Field(primary_key=True)
    embedding: list[float] = Field(sa_column=Column(Vector(EMBEDDING_DIMS), nullable=False))
    approved_at: datetime = Field(default_factory=_now, sa_column=Column(TIMESTAMP(timezone=True), nullable=False))


class KeywordMap(SQLModel, table=True):
    """One primary keyword maps to one URL per client, to stop cannibalisation across weeks."""

    __tablename__ = "keyword_map"

    client_id: str = Field(primary_key=True)
    primary_keyword: str = Field(primary_key=True)
    url: str
