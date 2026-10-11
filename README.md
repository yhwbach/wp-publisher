# wp-publisher

Takes a CSV of articles, validates each row, and schedules the good ones on a WordPress site through the REST API.
Yoast SEO title, meta description and focus keyword are set on each post. Every run writes a report showing what
happened to each row.

I wrote this for the Section 2 task of an SEO automation assessment. The `content_pipeline/` package is extra code
backing some of my written answers (internal link suggestions with pgvector, QA checks, per-client memory with mem0).
The publisher doesn't depend on it.

## Setup

You'll need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.template .env
```

Then fill in `WP_URL`, `WP_USER` and `WP_APP_PASSWORD` in `.env`.

On the WordPress side:

1. Create a user with the Editor role for the script.
2. Generate an Application Password for it under Users, Profile.
3. Copy `wordpress/yoast-rest-meta.php` into `wp-content/mu-plugins/`. Yoast doesn't allow writing its fields over
   REST by default, so without this the SEO fields are silently dropped.

## Usage

Check the CSV without posting anything:

```bash
uv run python -m publisher articles.csv --dry-run
```

Publish and save a report (CSV or JSON, based on the extension):

```bash
uv run python -m publisher articles.csv --report reports/report.csv
```

The exit code is 1 if WordPress rejected any row and 2 if the settings are missing or invalid.

Each row in the report ends up with one of these statuses:

- `posted`: created as a scheduled post
- `skipped_existing`: a post with that slug already exists, so it was left alone
- `validation_failed`: the row had problems and was never sent; `reasons` lists all of them
- `api_failed`: WordPress returned an error; the status code and message are in the report
- `dry_run_valid`: only with `--dry-run`, the row would have been posted

Running the same CSV twice is safe. Before posting, the script looks the slug up on the site and skips it if it's
already there.

## Trying it without a real site

`mock_wp_server.py` fakes the parts of the WordPress API the script uses. It returns a couple of 503s for one slug
to exercise the retries, and a 400 for another.

```bash
uv run python mock_wp_server.py --port 8089 &

WP_URL=http://localhost:8089 WP_ALLOW_HTTP=true WP_USER=bot WP_APP_PASSWORD=secret \
  WP_MIN_REQUEST_INTERVAL_SECONDS=0.1 uv run python -m publisher articles.csv
```

With the sample CSV you should get 4 posted, 6 failing validation and 1 rejected by the API. Run it again and the 4
come back as `skipped_existing`.

## Optional Postgres ledger

If `DATABASE_URL` is set, every published post is also recorded in Postgres, so later runs can skip it without
asking WordPress.

```bash
docker compose up -d
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5440/seo_pipeline
uv run alembic upgrade head
```

Migrations are managed with Alembic. After changing a model, generate a new one with
`uv run alembic revision --autogenerate -m "describe the change"` and check it over before applying it.

## Tests

```bash
uv run pytest
```
