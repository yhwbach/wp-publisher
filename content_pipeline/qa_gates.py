"""Deterministic QA gates that run on every draft before it reaches a human (Sections 3.2 and 3.3)."""

import re
from html import unescape
from typing import Literal

import numpy as np
from pydantic import BaseModel
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from content_pipeline.embeddings import Embedder, cosine
from content_pipeline.models import ClientPage, ClientStyleCorpus

Severity = Literal["block", "flag"]


class Finding(BaseModel):
    gate: str
    severity: Severity
    message: str


class QAReport(BaseModel):
    findings: list[Finding] = []

    @property
    def blocked(self) -> bool:
        return any(f.severity == "block" for f in self.findings)

    def add(self, gate: str, severity: Severity, message: str) -> None:
        self.findings.append(Finding(gate=gate, severity=severity, message=message))


class Source(BaseModel):
    id: str
    url: str
    quote: str


TAG_RE = re.compile(r"<[^>]+>")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
CITATION_RE = re.compile(r"\[\[src:([\w-]+)\]\]")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
CLAIM_RE = re.compile(
    r"\d+(?:\.\d+)?\s?(?:%|percent)|\$\s?\d|\b\d+\s+(?:out of|in)\s+\d+\b"
    r"|\b(?:study|survey|report|research|according to|data from)\b",
    re.IGNORECASE,
)
ARTIFACT_RE = re.compile(
    r"as an ai\b|\[insert|lorem ipsum|```|\*\*|^#{1,6}\s|in conclusion,|i hope this helps",
    re.IGNORECASE | re.MULTILINE,
)


def plain_text(html: str) -> str:
    return unescape(TAG_RE.sub(" ", html))


def check_artifacts(html: str, report: QAReport) -> None:
    if hits := sorted({m.group(0).lower() for m in ARTIFACT_RE.finditer(html)}):
        report.add("ai_artifacts", "block", f"leftover generation artifacts: {hits}")


def check_keyword(html: str, title: str, keyword: str, report: QAReport, max_density: float = 0.025) -> None:
    text = plain_text(html).lower()
    kw = keyword.lower()
    first_100 = " ".join(text.split()[:100])
    h2s = " ".join(re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.IGNORECASE | re.DOTALL)).lower()

    missing = [
        where
        for where, haystack in (("title", title.lower()), ("first 100 words", first_100), ("any H2", h2s))
        if kw not in haystack
    ]
    if "title" in missing or "first 100 words" in missing:
        report.add("keyword", "block", f"primary keyword missing from: {missing}")
    elif missing:
        report.add("keyword", "flag", f"primary keyword missing from: {missing}")

    words = max(len(text.split()), 1)
    density = text.count(kw) * len(kw.split()) / words
    if density > max_density:
        report.add("keyword", "flag", f"keyword density {density:.1%} looks stuffed")


def check_claims(html: str, sources: dict[str, Source], report: QAReport) -> None:
    """Every statistic-like sentence must cite a source, and its numbers must appear in that source's quote."""
    for sentence in SENTENCE_RE.split(plain_text(html)):
        if not CLAIM_RE.search(sentence):
            continue
        cited = CITATION_RE.findall(sentence)
        if not cited:
            report.add("claims", "block", f"unsourced claim: {sentence.strip()[:160]}")
            continue
        unknown = [c for c in cited if c not in sources]
        if unknown:
            report.add("claims", "block", f"citation to unknown source {unknown}: {sentence.strip()[:160]}")
            continue

        quote_numbers = {n.replace(",", "") for c in cited for n in NUMBER_RE.findall(sources[c].quote)}
        claim_numbers = {n.replace(",", "") for n in NUMBER_RE.findall(CITATION_RE.sub("", sentence))}
        if mismatched := sorted(claim_numbers - quote_numbers):
            report.add(
                "claims",
                "block",
                f"numbers {mismatched} not found in cited source(s) {cited}: {sentence.strip()[:160]}",
            )


async def check_tone_drift(
    session: AsyncSession,
    embed: Embedder,
    client_id: str,
    html: str,
    report: QAReport,
    flag_z: float = -2.0,
    block_z: float = -3.5,
) -> None:
    """Score the draft against the client's approved corpus, relative to how similar that corpus is to itself.

    Also blocks when the draft sits closer to another client's centroid (cross-client contamination).
    """
    by_client: dict[str, list[np.ndarray]] = {}
    rows = await session.exec(select(ClientStyleCorpus.client_id, ClientStyleCorpus.embedding))
    for cid, emb in rows:
        by_client.setdefault(cid, []).append(np.asarray(emb))

    own = by_client.get(client_id, [])
    if len(own) < 5:
        report.add("tone_drift", "flag", "fewer than 5 approved posts, tone check skipped")
        return

    draft = np.asarray(embed([plain_text(html)[:8000]])[0])
    centroid = np.mean(own, axis=0)
    baseline = np.array([cosine(e, centroid) for e in own])
    score = cosine(draft, centroid)
    z = (score - baseline.mean()) / (baseline.std() or 1e-6)

    if z < block_z:
        report.add("tone_drift", "block", f"tone far from client corpus (z={z:.1f})")
    elif z < flag_z:
        report.add("tone_drift", "flag", f"tone drifting from client corpus (z={z:.1f})")

    for other, embs in by_client.items():
        if other != client_id and cosine(draft, np.mean(embs, axis=0)) > score:
            report.add("tone_drift", "block", f"draft is closer to client '{other}' than to '{client_id}'")
            break


async def check_duplicates(
    session: AsyncSession,
    embed: Embedder,
    client_id: str,
    html: str,
    report: QAReport,
    block_at: float = 0.92,
    flag_at: float = 0.85,
) -> None:
    distance = col(ClientPage.embedding).cosine_distance(embed([plain_text(html)[:8000]])[0])
    nearest = (
        await session.exec(
            select(ClientPage.url, distance.label("distance"))
            .where(ClientPage.client_id == client_id)
            .order_by(distance)
            .limit(1)
        )
    ).first()
    if not nearest:
        return

    url, dist = nearest
    similarity = 1 - dist
    if similarity >= block_at:
        report.add("duplicate", "block", f"near-duplicate of {url} (cosine {similarity:.2f})")
    elif similarity >= flag_at:
        report.add("duplicate", "flag", f"possible cannibalisation with {url} (cosine {similarity:.2f})")
