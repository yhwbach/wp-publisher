from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from database.session import create_async_session
from publisher import wordpress
from publisher.ledger import PublishLedger, PublishLedgerRepository
from publisher.runner import BatchPublisher
from publisher.schemas import ArticleRow, WordPressPost, describe_errors
from publisher.settings import WordPressSettings
from publisher.wordpress import WordPressAPIError, WordPressClient

NOW = datetime(2026, 10, 11, tzinfo=UTC)
ALLOWED = ["blog", "guides", "news"]
CONTEXT = {"now": NOW, "allowed_categories": ALLOWED}


def make_row(**overrides: str) -> dict[str, str]:
    row = {
        "title": "Local SEO Checklist",
        "slug": "local-seo-checklist",
        "meta_title": "Local SEO Checklist (2027)",
        "meta_description": "A practical local SEO checklist.",
        "body_html": "<p>Hello</p>",
        "primary_keyword": "local seo checklist",
        "publish_date": "2027-01-15T09:00:00Z",
        "category": "guides",
    }
    return row | overrides


def reasons_for(row: dict[str, str]) -> list[str]:
    try:
        ArticleRow.model_validate(row, context=CONTEXT)
    except ValidationError as exc:
        return describe_errors(exc)
    return []


def test_valid_row_has_no_errors() -> None:
    assert reasons_for(make_row()) == []


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"body_html": "  "}, "body_html: required field is missing"),
        ({"title": "x" * 61}, "title: is 61 chars (max 60)"),
        ({"meta_title": "x" * 61}, "meta_title: is 61 chars"),
        ({"meta_description": "x" * 161}, "meta_description: is 161 chars"),
        ({"slug": "Best SEO Tools!"}, "invalid characters"),
        ({"slug": "double--hyphen"}, "invalid characters"),
        ({"slug": "trailing-"}, "invalid characters"),
        ({"publish_date": "2024-01-01T00:00:00Z"}, "not in the future"),
        ({"publish_date": "next tuesday"}, "publish_date: Input should be a valid datetime"),
        ({"category": "tutorials"}, "not in allow-list"),
    ],
)
def test_validation_rules(overrides: dict[str, str], expected: str) -> None:
    reasons = reasons_for(make_row(**overrides))
    assert any(expected in r for r in reasons), reasons


def test_boundaries_are_inclusive() -> None:
    assert reasons_for(make_row(title="x" * 60, meta_description="y" * 160)) == []


def test_collects_every_error_in_one_pass() -> None:
    assert len(reasons_for(make_row(title="x" * 61, category="nope", slug="Bad Slug"))) == 3


def test_post_maps_yoast_fields_and_converts_to_utc() -> None:
    article = ArticleRow.model_validate(make_row(publish_date="2027-01-15T09:00:00-05:00"), context=CONTEXT)
    post = WordPressPost.from_article(article, category_id=3)
    assert post.status == "future"
    assert post.date_gmt == "2027-01-15T14:00:00"
    assert post.categories == [3]
    assert post.meta["_yoast_wpseo_title"] == "Local SEO Checklist (2027)"
    assert post.meta["_yoast_wpseo_metadesc"] == "A practical local SEO checklist."


def test_settings_reject_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WP_URL", "http://client.com")
    monkeypatch.setenv("WP_USER", "bot")
    monkeypatch.setenv("WP_APP_PASSWORD", "secret")
    with pytest.raises(ValidationError, match="https"):
        WordPressSettings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> WordPressClient:
    monkeypatch.setattr(wordpress.asyncio, "sleep", AsyncMock())
    return WordPressClient("https://example.com", "u", "p", min_interval=0, backoff_base=0)


def respond(status: int, body: Any = None) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {})


def sample_post() -> WordPressPost:
    return WordPressPost.from_article(ArticleRow.model_validate(make_row(), context=CONTEXT), category_id=3)


async def test_retries_on_5xx_then_succeeds(client: WordPressClient) -> None:
    client.http.request = AsyncMock(side_effect=[respond(503), respond(502), respond(201, {"id": 7})])
    assert (await client.create_post(sample_post()))["id"] == 7
    assert client.http.request.call_count == 3


async def test_retries_on_timeout(client: WordPressClient) -> None:
    client.http.request = AsyncMock(side_effect=[httpx.ReadTimeout("slow"), respond(201, {"id": 8})])
    assert (await client.create_post(sample_post()))["id"] == 8


async def test_does_not_retry_4xx(client: WordPressClient) -> None:
    client.http.request = AsyncMock(return_value=respond(400, {"code": "rest_invalid_param", "message": "bad"}))
    with pytest.raises(WordPressAPIError) as exc:
        await client.create_post(sample_post())
    assert exc.value.status == 400
    assert client.http.request.call_count == 1


async def test_gives_up_after_max_retries(client: WordPressClient) -> None:
    client.http.request = AsyncMock(return_value=respond(500))
    with pytest.raises(WordPressAPIError) as exc:
        await client.create_post(sample_post())
    assert exc.value.status == 500
    assert client.http.request.call_count == client.max_retries + 1


def fake_wp(**overrides: Any) -> MagicMock:
    wp = AsyncMock(spec=WordPressClient)
    wp.find_post_by_slug.return_value = None
    wp.category_id.return_value = 3
    wp.create_post.return_value = {"id": 1, "link": "https://example.com/?p=1"}
    for name, value in overrides.items():
        setattr(getattr(wp, name), "side_effect" if isinstance(value, list) else "return_value", value)
    return wp


async def test_batch_continues_past_row_errors() -> None:
    wp = fake_wp(create_post=[WordPressAPIError(400, "rest_invalid_param: bad"), {"id": 2, "link": "x"}])
    rows = [make_row(slug="one"), make_row(slug="bad slug"), make_row(slug="two")]
    results = await BatchPublisher(wp, "acme", ALLOWED).run(rows, now=NOW)
    assert [r.status for r in results] == ["api_failed", "validation_failed", "posted"]
    assert results[0].http_status == 400


async def test_existing_post_on_site_is_skipped() -> None:
    wp = fake_wp(find_post_by_slug={"id": 99, "link": "x", "status": "future"})
    results = await BatchPublisher(wp, "acme", ALLOWED).run([make_row()], now=NOW)
    assert results[0].status == "skipped_existing"
    wp.create_post.assert_not_called()


async def test_duplicate_slug_within_csv_flagged() -> None:
    results = await BatchPublisher(None, "acme", ALLOWED).run([make_row(), make_row()], now=NOW)
    assert [r.status for r in results] == ["dry_run_valid", "validation_failed"]


@pytest.fixture
async def ledger() -> AsyncIterator[PublishLedgerRepository]:
    # Unit tests use in-memory SQLite; Postgres schema is managed by Alembic.
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[PublishLedger.__table__])
    async with create_async_session(engine)() as session:
        yield PublishLedgerRepository(session)
    await engine.dispose()


async def test_ledger_prevents_second_post_without_asking_wordpress(ledger: PublishLedgerRepository) -> None:
    first = await BatchPublisher(fake_wp(), "acme", ALLOWED, ledger).run([make_row()], now=NOW)
    assert first[0].status == "posted"
    entry = await ledger.get("acme", "local-seo-checklist")
    assert entry is not None and entry.status == "posted"

    wp = fake_wp()
    second = await BatchPublisher(wp, "acme", ALLOWED, ledger).run([make_row()], now=NOW)
    assert second[0].status == "skipped_existing"
    wp.find_post_by_slug.assert_not_called()
    wp.create_post.assert_not_called()


async def test_ledger_records_failures(ledger: PublishLedgerRepository) -> None:
    wp = fake_wp(create_post=[WordPressAPIError(403, "rest_forbidden: nope")])
    await BatchPublisher(wp, "acme", ALLOWED, ledger).run([make_row()], now=NOW)
    entry = await ledger.get("acme", "local-seo-checklist")
    assert entry is not None and entry.status == "failed" and "rest_forbidden" in (entry.last_error or "")


async def test_batch_aborts_after_repeated_auth_failures() -> None:
    wp = fake_wp(find_post_by_slug=[WordPressAPIError(401, "rest_not_logged_in")] * 10)
    rows = [make_row(slug=f"post-{i}") for i in range(5)]
    results = await BatchPublisher(wp, "acme", ALLOWED, max_consecutive_fatal=3).run(rows, now=NOW)
    assert [r.status for r in results] == ["api_failed"] * 5
    assert wp.find_post_by_slug.call_count == 3
    assert "batch aborted" in results[4].reasons[0]
