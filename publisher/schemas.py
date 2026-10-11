import hashlib
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    field_validator,
)

RowStatus = Literal["posted", "skipped_existing", "validation_failed", "api_failed", "dry_run_valid"]

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _required(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
    if value in (None, ""):
        raise ValueError("required field is missing")
    return value


def _max_chars(limit: int) -> AfterValidator:
    def check(value: str) -> str:
        if len(value) > limit:
            raise ValueError(f"is {len(value)} chars (max {limit})")
        return value

    return AfterValidator(check)


def _valid_slug(value: str) -> str:
    if not SLUG_RE.fullmatch(value):
        raise ValueError(f"'{value}' has invalid characters (lowercase a-z, 0-9 and single hyphens only)")
    return value


RequiredStr = Annotated[str, BeforeValidator(_required)]


class ArticleRow(BaseModel):
    """One CSV row. Validate with context={"now": datetime, "allowed_categories": list[str]}."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    title: Annotated[RequiredStr, _max_chars(60)]
    slug: Annotated[RequiredStr, AfterValidator(_valid_slug)]
    meta_title: Annotated[RequiredStr, _max_chars(60)]
    meta_description: Annotated[RequiredStr, _max_chars(160)]
    body_html: RequiredStr
    primary_keyword: RequiredStr
    publish_date: Annotated[datetime, BeforeValidator(_required)]
    category: RequiredStr

    @field_validator("publish_date")
    @classmethod
    def publish_date_in_future(cls, value: datetime, info: ValidationInfo) -> datetime:
        # Naive timestamps are assumed to be UTC.
        value = value if value.tzinfo else value.replace(tzinfo=UTC)
        now: datetime = (info.context or {}).get("now") or datetime.now(UTC)
        if value <= now:
            raise ValueError(f"{value.isoformat()} is not in the future")
        return value

    @field_validator("category")
    @classmethod
    def category_allowed(cls, value: str, info: ValidationInfo) -> str:
        allowed: list[str] | None = (info.context or {}).get("allowed_categories")
        if allowed is not None and value not in allowed:
            raise ValueError(f"'{value}' not in allow-list {allowed}")
        return value

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def describe_errors(exc: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg'].removeprefix('Value error, ')}"
        for err in exc.errors()
    ]


class WordPressPost(BaseModel):
    title: str
    slug: str
    content: str
    status: Literal["draft", "future"] = "future"
    date_gmt: str
    categories: list[int]
    meta: dict[str, str]

    @classmethod
    def from_article(cls, article: ArticleRow, category_id: int) -> Self:
        return cls(
            title=article.title,
            slug=article.slug,
            content=article.body_html,
            date_gmt=article.publish_date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            categories=[category_id],
            meta={
                "_yoast_wpseo_title": article.meta_title,
                "_yoast_wpseo_metadesc": article.meta_description,
                "_yoast_wpseo_focuskw": article.primary_keyword,
            },
        )


class RowResult(BaseModel):
    row_number: int
    slug: str
    title: str
    status: RowStatus | None = None
    reasons: list[str] = []
    http_status: int | None = None
    post_id: int | None = None
    post_link: str | None = None

    def mark(self, status: RowStatus, **fields: Any) -> Self:
        for key, value in {"status": status, **fields}.items():
            setattr(self, key, value)
        return self
