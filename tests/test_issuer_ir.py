from datetime import date

from halka_arz_advisor.issuer_ir.cache import IssuerIrCache
from halka_arz_advisor.issuer_ir.crawler import discover_pdf_links
from halka_arz_advisor.issuer_ir.ingest import search_and_ingest
from halka_arz_advisor.issuer_ir.registry import IssuerIrSource
from halka_arz_advisor.kap.extraction import (
    FieldObservation,
    SourceRef,
    apply_lower_authority_fallback,
    build_extracted_facts,
)
from halka_arz_advisor.kap.pdf import PdfCache
from halka_arz_advisor.probe.config import ProbeConfig

SRC_KAP = SourceRef("approved_prospectus", "d-p", "url-p", 1)
SRC_IR = SourceRef("approved_prospectus", "issuer_ir:x", "url-ir", 1, source_system="issuer_ir")


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=1, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


# --------------------------------------------------------------------------
# 1. Issuer-page discovery
# --------------------------------------------------------------------------


def test_discover_pdf_links_classifies_and_excludes_other_domains():
    html = """
    <html><body>
      <a href="https://assets.example.com/doc/izahname.pdf">İzahnameDosyayı İndir</a>
      <a href="/doc/tasarruf-sahiplerine-satis-duyurusu.pdf">Tasarruf Sahiplerine Satış Duyurusunu Görüntüle</a>
      <a href="/doc/ek7-fonun-kullanim-yerlerine-iliskin.pdf">EK 7: Fonun Kullanım Yerlerine İlişkin Rapor</a>
      <a href="https://aggregator.example.net/summary.pdf">Halka Arz Özeti (üçüncü taraf)</a>
      <a href="/genel-kurul-toplanti-tutanagi.pdf">Genel Kurul Toplantı Tutanağı</a>
    </body></html>
    """
    links = discover_pdf_links(html, "https://www.example.com/yatirimci-iliskileri/halka-arz", "example.com")

    by_url = {link.url: link.document_type for link in links}
    assert by_url["https://assets.example.com/doc/izahname.pdf"] == "approved_prospectus"
    assert by_url["https://www.example.com/doc/tasarruf-sahiplerine-satis-duyurusu.pdf"] == "investor_sale_announcement"
    assert by_url["https://www.example.com/doc/ek7-fonun-kullanim-yerlerine-iliskin.pdf"] == "use_of_proceeds_report"
    # A different-domain link and an unclassifiable one are both excluded outright.
    assert "https://aggregator.example.net/summary.pdf" not in by_url
    assert "https://www.example.com/genel-kurul-toplanti-tutanagi.pdf" not in by_url
    assert len(links) == 3


# --------------------------------------------------------------------------
# 2. Duplicate-PDF dedup by content hash
# --------------------------------------------------------------------------


def test_search_and_ingest_skips_content_identical_to_a_known_hash(httpx_mock, build_pdf_bytes, tmp_path):
    source = IssuerIrSource(
        ticker="TESTX", company_name="Test Enerji A.Ş.",
        ipo_page_url="https://www.testx.com/halka-arz", allowed_domain="testx.com",
    )
    page_html = """
    <html><body>
      <a href="/doc/ek3-finansal-tablolar.pdf">EK 3: Finansal Tablolar ve Bağımsız Denetim Raporu</a>
    </body></html>
    """
    pdf_bytes = build_pdf_bytes(text="ayni icerik")
    httpx_mock.add_response(url="https://www.testx.com/halka-arz", html=page_html)
    httpx_mock.add_response(url="https://www.testx.com/doc/ek3-finansal-tablolar.pdf", content=pdf_bytes)

    import hashlib

    known_hash = hashlib.sha256(pdf_bytes).hexdigest()

    cache = IssuerIrCache(tmp_path / "issuer_ir")
    pdf_cache = PdfCache(tmp_path / "pdfs")

    outcome = search_and_ingest(
        "ipo:TESTX:2026 / 6",
        source,
        ["financial_statement_attachment"],
        cache=cache,
        pdf_cache=pdf_cache,
        known_content_hashes=frozenset({known_hash}),
        config=fast_config(),
        reference_date=date(2026, 8, 7),
    )

    # Downloaded (to compute its hash at all) but recognized as
    # byte-identical to something already known — never counted as a
    # new recovery, never stored as a second copy.
    assert outcome.recovered_document_types == ()
    assert outcome.duplicate_of_known_content == ("https://www.testx.com/doc/ek3-finansal-tablolar.pdf",)
    assert len(outcome.disclosures) == 0

    entry = cache.get("TESTX")
    assert entry is not None
    assert entry.ingested == ()


# --------------------------------------------------------------------------
# 3. KAP-authority conflict: issuer_ir never overrides a KAP value
# --------------------------------------------------------------------------


def test_apply_lower_authority_fallback_never_overrides_a_kap_value():
    kap_facts = build_extracted_facts(
        {"offering_price": FieldObservation(76.6, "76,60 TL", SRC_KAP)}, None
    )
    # issuer_ir disagrees on offering_price (must be ignored) but has a
    # field KAP has nothing for at all (must be used).
    issuer_ir_facts = build_extracted_facts(
        {
            "offering_price": FieldObservation(99.0, "99,00 TL", SRC_IR),
            "capital_increase_ratio": FieldObservation(50.0, "%50", SRC_IR),
        },
        None,
    )

    merged = apply_lower_authority_fallback(kap_facts, issuer_ir_facts)

    assert merged.offering_price.value == 76.6
    assert merged.offering_price.source.source_system == "kap"
    assert merged.capital_increase_ratio.value == 50.0
    assert merged.capital_increase_ratio.source.source_system == "issuer_ir"


def test_apply_lower_authority_fallback_handles_missing_sides():
    facts = build_extracted_facts({"offering_price": FieldObservation(76.6, "x", SRC_KAP)}, None)
    assert apply_lower_authority_fallback(facts, None) is facts
    assert apply_lower_authority_fallback(None, facts) is facts
    assert apply_lower_authority_fallback(None, None) is None
