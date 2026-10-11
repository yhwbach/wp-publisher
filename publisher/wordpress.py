import asyncio
import logging
import time
from typing import Any, Self

import httpx

from publisher.schemas import WordPressPost
from publisher.settings import WordPressSettings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class WordPressAPIError(Exception):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class WordPressClient:
    def __init__(
        self,
        base_url: str,
        user: str,
        app_password: str,
        min_interval: float = 1.0,
        max_retries: int = 4,
        timeout: float = 30.0,
        backoff_base: float = 2.0,
    ):
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/wp-json/wp/v2",
            auth=(user, app_password),
            timeout=timeout,
            headers={"User-Agent": "seo-bulk-publisher/1.0", "Accept": "application/json"},
        )
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._last_request = 0.0
        self._category_cache: dict[str, int] = {}

    @classmethod
    def from_settings(cls, settings: WordPressSettings) -> Self:
        return cls(
            str(settings.WP_URL),
            settings.WP_USER,
            settings.WP_APP_PASSWORD.get_secret_value(),
            min_interval=settings.WP_MIN_REQUEST_INTERVAL_SECONDS,
            max_retries=settings.WP_MAX_RETRIES,
            timeout=settings.WP_TIMEOUT_SECONDS,
            backoff_base=settings.WP_BACKOFF_BASE_SECONDS,
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _pace(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error = WordPressAPIError(None, "no attempt made")

        for attempt in range(self.max_retries + 1):
            await self._pace()
            try:
                resp = await self.http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = WordPressAPIError(None, f"{type(exc).__name__}: {exc}")
                delay = self.backoff_base**attempt
            else:
                if resp.status_code not in RETRYABLE_STATUS:
                    return resp
                last_error = WordPressAPIError(resp.status_code, _error_message(resp))
                delay = _retry_after(resp) or self.backoff_base**attempt

            if attempt < self.max_retries:
                log.warning(
                    "%s %s failed (%s), retry %d/%d in %.1fs",
                    method,
                    path,
                    last_error.message,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                await asyncio.sleep(delay)

        raise last_error

    async def category_id(self, slug: str) -> int:
        if slug not in self._category_cache:
            resp = await self._request("GET", "/categories", params={"slug": slug})
            _raise_for_status(resp)
            matches = resp.json()
            if not matches:
                raise WordPressAPIError(404, f"category '{slug}' does not exist on site")
            self._category_cache[slug] = matches[0]["id"]
        return self._category_cache[slug]

    async def find_post_by_slug(self, slug: str) -> dict[str, Any] | None:
        # status=any so drafts/scheduled posts from a previous run are found too.
        resp = await self._request(
            "GET",
            "/posts",
            params={"slug": slug, "status": "any", "context": "edit", "_fields": "id,link,status"},
        )
        _raise_for_status(resp)
        posts = resp.json()
        return posts[0] if posts else None

    async def create_post(self, post: WordPressPost) -> dict[str, Any]:
        resp = await self._request("POST", "/posts", json=post.model_dump())
        _raise_for_status(resp, expected=(201,))
        return resp.json()


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return f"{body.get('code', 'error')}: {body.get('message', '')}".strip()
    except ValueError:
        pass
    return (resp.text or resp.reason_phrase or "").strip()[:300]


def _raise_for_status(resp: httpx.Response, expected: tuple[int, ...] = (200,)) -> None:
    if resp.status_code not in expected:
        raise WordPressAPIError(resp.status_code, _error_message(resp))
