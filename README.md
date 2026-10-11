# WP Bulk Publisher

Section 2 of the SEO Automation assessment: validate a CSV of AI-generated articles and schedule the valid ones on
WordPress through the REST API, with Yoast SEO fields, pacing, retries, idempotency and a per-row report.

`content_pipeline/` holds supporting code for the Section 3/4 answers (pgvector internal-link retrieval, QA gates,
mem0 client memory). It isn't needed to run the publisher.

## Layout

```
publisher/
  settings.py      pydantic-settings (WP_*, CLIENT_ID, ALLOWED_CATEGORIES, DATABASE_URL)
  schemas.py       ArticleRow (validation), WordPressPost (REST payload), RowResult (report row)
  wordpress.py     async httpx client: App Password auth, pacing, retries/backoff, Retry-After
  runner.py        BatchPublisher: validate, idempotency check, POST, record; circuit breaker
  ledger.py        PublishLedger SQLModel + async repository (optional, enabled by DATABASE_URL)
  cli.py           python -m publisher
database/
  session.py       async engine / AsyncSession factory
  migrations/      Alembic (async template)
content_pipeline/  pgvector link pool, QA gates (claims, tone drift, duplicates), mem0 memory
wordpress/yoast-rest-meta.php   mu-plugin: makes Yoast meta writable over REST (required)
mock_wp_server.py  local fake of /wp-json/wp/v2 with injected 503s and a 400
articles.csv       sample rows covering every validation rule
```

## Setup

```bash
uv sync                       # add --extra pipeline for content_pipeline (mem0, openai, numpy)
cp .env.template .env         # fill in WP_URL / WP_USER / WP_APP_PASSWORD
```

On the WordPress site: create an Editor-role user, generate an Application Password (Users → Profile), and drop
`wordpress/yoast-rest-meta.php` into `wp-content/mu-plugins/`.

## Run

```bash
uv run python -m publisher articles.csv --dry-run                 # validate only
uv run python -m publisher articles.csv --report reports/report.csv
uv run python -m publisher articles.csv --report reports/report.json
```

Exit code 1 if any row failed at the API level, 2 if the WordPress settings are invalid.

### Try it locally against the mock

```bash
uv run python mock_wp_server.py --port 8089 &
WP_URL=http://localhost:8089 WP_ALLOW_HTTP=true WP_USER=bot WP_APP_PASSWORD=secret \
WP_MIN_REQUEST_INTERVAL_SECONDS=0.1 uv run python -m publisher articles.csv
```

Expected: 4 posted (one after two 503 retries), 6 validation_failed, 1 api_failed (400). A second run reports the
4 as `skipped_existing`.

### With the Postgres ledger

```bash
docker compose up -d                       # pgvector/pgvector:pg17 on :5440
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5440/seo_pipeline
uv run alembic upgrade head
uv run python -m publisher articles.csv
```

New migrations: change the SQLModel models, then `uv run alembic revision --autogenerate -m "..."` and review the
generated file.

## Tests

```bash
uv run pytest
```

## Report statuses

| status              | meaning                                                         |
|---------------------|-----------------------------------------------------------------|
| `posted`            | created as a scheduled (`future`) post                          |
| `skipped_existing`  | slug already exists on the site or in the ledger, not duplicated |
| `validation_failed` | row rejected before any API call; `reasons` lists every problem |
| `api_failed`        | WordPress error; `http_status` + `code: message` in `reasons`   |
| `dry_run_valid`     | `--dry-run` only: row would be posted                           |
