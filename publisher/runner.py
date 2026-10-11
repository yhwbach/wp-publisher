import csv
import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from publisher.ledger import LedgerStatus, PublishLedgerRepository
from publisher.schemas import ArticleRow, RowResult, WordPressPost, describe_errors
from publisher.wordpress import WordPressAPIError, WordPressClient

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = frozenset(ArticleRow.model_fields)
# Connection errors (None) and auth failures won't fix themselves on the next row.
FATAL_STATUSES = frozenset({None, 401, 403})


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    # utf-8-sig strips the BOM that Excel/Sheets exports add.
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV is missing column(s): {', '.join(sorted(missing))}")
        return list(reader)


class BatchPublisher:
    def __init__(
        self,
        client: WordPressClient | None,
        client_id: str,
        allowed_categories: list[str],
        ledger: PublishLedgerRepository | None = None,
        run_id: UUID | None = None,
        max_consecutive_fatal: int = 3,
    ) -> None:
        self.client = client
        self.client_id = client_id
        self.allowed_categories = allowed_categories
        self.ledger = ledger
        self.run_id = run_id or uuid4()
        self.max_consecutive_fatal = max_consecutive_fatal
        self._consecutive_fatal = 0
        self._abort_reason: str | None = None

    async def run(self, rows: list[dict[str, str]], now: datetime | None = None) -> list[RowResult]:
        context = {"now": now or datetime.now(UTC), "allowed_categories": self.allowed_categories}
        results: list[RowResult] = []
        seen_slugs: set[str] = set()

        for i, row in enumerate(rows, start=1):
            slug = (row.get("slug") or "").strip()
            result = RowResult(row_number=i, slug=slug, title=(row.get("title") or "").strip())
            results.append(result)

            reasons: list[str] = []
            article: ArticleRow | None = None
            try:
                article = ArticleRow.model_validate(row, context=context)
            except ValidationError as exc:
                reasons = describe_errors(exc)
            if slug and slug in seen_slugs:
                reasons.append(f"slug: duplicate '{slug}' within this CSV")
            seen_slugs.add(slug)

            if reasons or article is None:
                result.mark("validation_failed", reasons=reasons)
                log.info("row %d (%s): validation_failed - %s", i, slug, "; ".join(reasons))
                continue

            if self.client is None:
                result.mark("dry_run_valid")
                continue

            if self._abort_reason:
                result.mark("api_failed", reasons=[f"not attempted, batch aborted: {self._abort_reason}"])
                continue

            await self._publish(article, result)

        return results

    async def _publish(self, article: ArticleRow, result: RowResult) -> None:
        assert self.client is not None
        try:
            if not await self._already_published(article, result):
                await self._record(article, "pending")
                category_id = await self.client.category_id(article.category)
                post = await self.client.create_post(WordPressPost.from_article(article, category_id))
                result.mark("posted", http_status=201, post_id=post.get("id"), post_link=post.get("link"))
                await self._record(article, "posted", wp_post_id=result.post_id)
            self._consecutive_fatal = 0
            log.info("row %d (%s): %s", result.row_number, article.slug, result.status)
        except WordPressAPIError as exc:
            result.mark("api_failed", http_status=exc.status, reasons=[exc.message])
            await self._record(article, "failed", last_error=exc.message)
            log.error("row %d (%s): api_failed [%s] %s", result.row_number, article.slug, exc.status, exc.message)
            self._track_fatal(exc)
        except Exception as exc:  # one bad row must not kill the batch
            result.mark("api_failed", reasons=[f"unexpected error: {exc!r}"])
            log.exception("row %d (%s): unexpected error", result.row_number, article.slug)

    def _track_fatal(self, exc: WordPressAPIError) -> None:
        if exc.status not in FATAL_STATUSES:
            self._consecutive_fatal = 0
            return
        self._consecutive_fatal += 1
        if self._consecutive_fatal >= self.max_consecutive_fatal:
            self._abort_reason = (
                f"{self._consecutive_fatal} consecutive failures, last was [{exc.status}] {exc.message}"
            )
            log.error("Aborting batch: %s", self._abort_reason)

    async def _already_published(self, article: ArticleRow, result: RowResult) -> bool:
        assert self.client is not None
        if self.ledger and (entry := await self.ledger.get(self.client_id, article.slug)) and entry.status == "posted":
            note = (
                "already posted"
                if entry.content_hash == article.content_hash
                else ("already posted, content changed since (updates are not automated)")
            )
            result.mark("skipped_existing", post_id=entry.wp_post_id, reasons=[f"ledger: {note}"])
            return True

        # The site is checked too: catches posts created by hand or by a run without the ledger.
        if existing := await self.client.find_post_by_slug(article.slug):
            result.mark(
                "skipped_existing",
                post_id=existing["id"],
                post_link=existing.get("link"),
                reasons=[f"post already exists on site (status={existing.get('status')})"],
            )
            return True
        return False

    async def _record(
        self,
        article: ArticleRow,
        status: LedgerStatus,
        wp_post_id: int | None = None,
        last_error: str | None = None,
    ) -> None:
        if self.ledger:
            await self.ledger.record(
                self.client_id, article.slug, article.content_hash, self.run_id, status, wp_post_id, last_error
            )


def write_report(results: list[RowResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".json":
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": Counter(r.status for r in results),
            "rows": [r.model_dump() for r in results],
        }
        path.write_text(json.dumps(report, indent=2))
        return

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(RowResult.model_fields))
        writer.writeheader()
        for r in results:
            writer.writerow({**r.model_dump(), "reasons": " | ".join(r.reasons)})
