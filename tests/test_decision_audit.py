from datetime import datetime

from halka_arz_advisor.decision.audit import CompanyDecisionInputs, audit_company
from halka_arz_advisor.decision.catalog import FEATURE_CATALOG
from halka_arz_advisor.kap.attachments import KapAttachment
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef, build_extracted_facts
from halka_arz_advisor.kap.models import KapDisclosure

RECORD_ID = "ipo:QUICK:2026 / 7"

SRC_P = SourceRef("approved_prospectus", "d-approved_prospectus", "url-p", 1)
SRC_A = SourceRef("investor_sale_announcement", "d-investor_sale_announcement", "url-a", 3)


def _attachment(obj_id: str = "obj-1") -> KapAttachment:
    return KapAttachment(
        name="Izahname.pdf", url=f"https://www.kap.org.tr/tr/api/file/download/{obj_id}",
        content_type="application/pdf", document_role="primary_candidate", obj_id=obj_id,
    )


def _disclosure(*, document_type: str, pdf_status: str = "ok") -> KapDisclosure:
    return KapDisclosure(
        disclosure_id=f"d-{document_type}",
        disclosure_index=1,
        published_at=datetime(2026, 7, 1),
        company_name="QUİCK SİGORTA A.Ş.",
        ticker="QUICK",
        title="test",
        summary="",
        document_type=document_type,
        notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(),
        matched_spk_record_id=RECORD_ID,
        match_method="ticker",
        raw={},
        attachments=(_attachment(),),
        primary_document=_attachment(),
        pdf_status=pdf_status,
    )


def _inputs(*, facts=None, disclosures=()) -> CompanyDecisionInputs:
    return CompanyDecisionInputs(
        spk_record_id=RECORD_ID, spk_record=None, application_record=None, facts=facts, disclosures=disclosures
    )


def _result(results, feature_id: str):
    return next(r for r in results if r.feature_id == feature_id)


def test_audit_company_evaluates_every_catalog_feature_in_order():
    results = audit_company(_inputs())
    assert [r.feature_id for r in results] == [spec.feature_id for spec in FEATURE_CATALOG]


def test_available_when_field_extracted_with_evidence():
    facts = build_extracted_facts(
        {
            "offering_price": FieldObservation(76.6, "76,60 TL", SRC_P),
            "currency": FieldObservation("TRY", "76,60 TL", SRC_P),
        },
        None,
    )
    disclosures = (_disclosure(document_type="approved_prospectus"),)
    result = _result(audit_company(_inputs(facts=facts, disclosures=disclosures)), "offering_price")

    assert result.status == "AVAILABLE"
    price_evidence = next(e for e in result.evidence if e.field_name == "kap_extraction.offering_price")
    assert price_evidence.value == 76.6
    assert price_evidence.extraction_method == "digital"


def test_derivable_computes_implied_offer_size_value():
    facts = build_extracted_facts(
        {
            "offering_price": FieldObservation(10.0, "10 TL", SRC_P),
            "currency": FieldObservation("TRY", "10 TL", SRC_P),
            "total_offered_shares": FieldObservation(1000.0, "1000", SRC_P),
        },
        None,
    )
    disclosures = (_disclosure(document_type="approved_prospectus"),)
    result = _result(audit_company(_inputs(facts=facts, disclosures=disclosures)), "implied_offer_size_value")

    assert result.status == "DERIVABLE"
    assert result.evidence[0].value == 10.0 * 1000.0
    assert result.missing_dependencies == ()


def test_derived_feature_propagates_missing_dependency():
    facts = build_extracted_facts(
        {
            "offering_price": FieldObservation(10.0, "10 TL", SRC_P),
            "currency": FieldObservation("TRY", "10 TL", SRC_P),
        },
        None,
    )
    disclosures = (_disclosure(document_type="approved_prospectus"),)
    result = _result(audit_company(_inputs(facts=facts, disclosures=disclosures)), "implied_offer_size_value")

    assert result.status != "DERIVABLE"
    assert "total_offered_shares" in result.missing_dependencies


def test_sector_classification_wires_to_classify_sector_deterministically():
    # QUİCK SİGORTA A.Ş. (the _disclosure default) matches the insurance
    # pattern in kap.sector.classify_sector — no kap_extraction field, no
    # PDF text read, just the already-flowing company_name.
    disclosures = (_disclosure(document_type="approved_prospectus"),)
    result = _result(audit_company(_inputs(disclosures=disclosures)), "sector_classification")

    assert result.status == "AVAILABLE"
    sector_evidence = next(e for e in result.evidence if e.field_name == "kap_sector.classification")
    assert sector_evidence.value == "insurance"
    assert sector_evidence.disclosure_id is None  # not sourced from a specific document/page


def test_sector_classification_missing_document_when_no_company_name_at_all():
    result = _result(audit_company(_inputs()), "sector_classification")
    assert result.status == "MISSING_DOCUMENT"


def test_conflicted_preserves_both_observations():
    facts = build_extracted_facts(
        {"offering_price": FieldObservation(80.0, "80 TL", SRC_P)},
        {"offering_price": FieldObservation(76.6, "76,60 TL", SRC_A)},
    )
    disclosures = (
        _disclosure(document_type="approved_prospectus"),
        _disclosure(document_type="investor_sale_announcement"),
    )
    result = _result(audit_company(_inputs(facts=facts, disclosures=disclosures)), "offering_price")

    assert result.status == "CONFLICTED"
    price_evidence = next(e for e in result.evidence if e.field_name == "kap_extraction.offering_price")
    assert set(price_evidence.value) == {80.0, 76.6}
