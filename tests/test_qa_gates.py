from content_pipeline.internal_links import LinkCandidate, validate_internal_links
from content_pipeline.qa_gates import QAReport, Source, check_artifacts, check_claims, check_keyword

SOURCES = {"bls-2025": Source(id="bls-2025", url="https://example.org/bls", quote="Employment grew 4.2% in 2025.")}


def test_sourced_claim_with_matching_number_passes() -> None:
    report = QAReport()
    check_claims("<p>Employment grew 4.2% in 2025 [[src:bls-2025]].</p>", SOURCES, report)
    assert report.findings == []


def test_unsourced_stat_blocks() -> None:
    report = QAReport()
    check_claims("<p>73% of marketers agree.</p>", SOURCES, report)
    assert report.blocked


def test_number_not_in_source_blocks() -> None:
    report = QAReport()
    check_claims("<p>Employment grew 6.8% in 2025 [[src:bls-2025]].</p>", SOURCES, report)
    assert report.blocked and "6.8" in report.findings[0].message


def test_invented_source_blocks() -> None:
    report = QAReport()
    check_claims("<p>A Stanford study found 40% growth [[src:stanford]].</p>", SOURCES, report)
    assert report.blocked


def test_keyword_missing_from_title_blocks() -> None:
    report = QAReport()
    check_keyword("<p>local seo tips here</p><h2>local seo</h2>", "Ten Tips", "local seo", report)
    assert report.blocked


def test_artifacts_block() -> None:
    report = QAReport()
    check_artifacts("<p>As an AI language model, **bold**</p>", report)
    assert report.findings[0].severity == "block"


def test_hallucinated_internal_link_rejected() -> None:
    pool = [
        LinkCandidate(url=f"https://client.com/p{i}", title="t", summary="s", score=0.9, is_money_page=False)
        for i in range(5)
    ]
    html = "".join(f'<a href="https://client.com/p{i}">x</a>' for i in range(3))
    assert validate_internal_links(html, pool, "client.com") == []
    errors = validate_internal_links(html + '<a href="https://client.com/made-up">y</a>', pool, "client.com")
    assert any("not in the approved pool" in e for e in errors)
