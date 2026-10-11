"""Retrieve a small, relevant internal-link pool per post instead of sending the whole sitemap."""

import re
from urllib.parse import urlparse

from pydantic import BaseModel
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from content_pipeline.embeddings import Embedder
from content_pipeline.models import ClientPage

HREF_RE = re.compile(r'<a\s[^>]*href="([^"]+)"', re.IGNORECASE)


class LinkCandidate(BaseModel):
    url: str
    title: str
    summary: str
    score: float
    is_money_page: bool

    def as_prompt_line(self) -> str:
        return f"- {self.url} | {self.title} | {self.summary}"


async def link_candidates(
    session: AsyncSession,
    embed: Embedder,
    client_id: str,
    brief_text: str,
    k: int = 12,
    exclude: list[str] | None = None,
    money_page_boost: float = 0.05,
) -> list[LinkCandidate]:
    distance = col(ClientPage.embedding).cosine_distance(embed([brief_text])[0])
    statement = (
        select(ClientPage, distance.label("distance"))
        .where(
            ClientPage.client_id == client_id,
            ClientPage.http_status == 200,
            col(ClientPage.indexable).is_(True),
            col(ClientPage.url).not_in(exclude or []),
        )
        .order_by(distance)
        .limit(k * 3)
    )

    candidates = [
        LinkCandidate(
            url=page.url,
            title=page.title,
            summary=page.summary,
            score=1 - dist + (money_page_boost if page.is_money_page else 0.0),
            is_money_page=page.is_money_page,
        )
        for page, dist in await session.exec(statement)
    ]
    return sorted(candidates, key=lambda c: c.score, reverse=True)[:k]


def validate_internal_links(
    html: str,
    allowed: list[LinkCandidate],
    site_host: str,
    min_links: int = 3,
    max_links: int = 5,
) -> list[str]:
    allowed_urls = {c.url.rstrip("/") for c in allowed}
    internal = [href.rstrip("/") for href in HREF_RE.findall(html) if urlparse(href).netloc in ("", site_host)]
    unique = set(internal)

    errors: list[str] = []
    if not min_links <= len(unique) <= max_links:
        errors.append(f"{len(unique)} unique internal links (expected {min_links}-{max_links})")
    if invented := sorted(unique - allowed_urls):
        errors.append(f"links not in the approved pool (possibly hallucinated): {invented}")
    if len(internal) != len(unique):
        errors.append("same internal URL linked more than once")
    return errors
