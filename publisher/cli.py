"""Validate a CSV of articles and schedule the valid ones on WordPress.

python -m publisher articles.csv --report reports/report.csv
python -m publisher articles.csv --dry-run
"""

import argparse
import asyncio
import logging
from collections import Counter
from contextlib import AsyncExitStack
from pathlib import Path

from pydantic import ValidationError

from database.session import get_db
from publisher.ledger import PublishLedgerRepository
from publisher.runner import BatchPublisher, read_rows, write_report
from publisher.settings import PublisherSettings, WordPressSettings
from publisher.wordpress import WordPressClient

log = logging.getLogger("publisher")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--report", type=Path, default=Path("reports/report.csv"), help=".csv or .json")
    parser.add_argument("--dry-run", action="store_true", help="validate only, no API or DB calls")
    parser.add_argument("--no-ledger", action="store_true", help="skip the Postgres ledger even if DATABASE_URL is set")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    settings = PublisherSettings()
    rows = read_rows(args.csv_path)

    async with AsyncExitStack() as stack:
        client: WordPressClient | None = None
        ledger: PublishLedgerRepository | None = None

        if not args.dry_run:
            try:
                client = WordPressClient.from_settings(WordPressSettings())  # type: ignore[call-arg]
            except ValidationError as exc:
                log.error("WordPress settings invalid (see .env.template):\n%s", exc)
                return 2
            stack.push_async_callback(client.aclose)

            if settings.DATABASE_URL and not args.no_ledger:
                session = await stack.enter_async_context(get_db(str(settings.DATABASE_URL)))
                ledger = PublishLedgerRepository(session)

        publisher = BatchPublisher(client, settings.CLIENT_ID, settings.ALLOWED_CATEGORIES, ledger)
        results = await publisher.run(rows)

    write_report(results, args.report)
    counts = Counter(r.status for r in results)
    log.info("Run %s finished with %s. Report written to %s", publisher.run_id, dict(counts), args.report)

    # Non-zero exit on API failures so cron/CI alerting picks it up.
    return 1 if counts["api_failed"] else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.DEBUG if args.verbose else logging.WARNING)
    return asyncio.run(run(args))
