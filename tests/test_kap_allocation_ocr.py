"""Tests for halka_arz_advisor.kap.allocation_ocr — the scoped,
on-demand deep-OCR fallback that recovers allocation-mechanics fields
sitting past a normal OCR page budget. Never requires the real
Tesseract CLI (same mocking convention as tests/test_kap_ocr.py)."""

import io
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import halka_arz_advisor.kap.ocr as ocr_module
from halka_arz_advisor.kap.allocation_ocr import recover_allocation_sections
from halka_arz_advisor.kap.attachments import KapAttachment
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.ocr import OcrCache, OcrConfig
from halka_arz_advisor.kap.pdf import PdfCache


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# Real shape (paraphrased from EKDMR's actual 2026 İzahname §25.2.3(a),
# already used verbatim in tests/test_kap_extraction.py).
ALLOCATION_TABLE_TEXT = (
    "Halka arz edilecek toplam 52.000.000 TL nominal değerli payların; "
    "20.800.000 TL nominal değerdeki kısmı (40%) Yurt İçi Bireysel Yatırımcılara, "
    "5.200.000 TL nominal değerdeki kısmı (10%) Yüksek Talepte Bulunacak Yatırımcı Grubu'na, "
    "15.600.000 TL nominal değerdeki kısmı (30%) Yurt İçi Kurumsal Yatırımcılara, "
    "10.400.000 TL nominal değerdeki kısmı (20%) Yurt Dışı Kurumsal Yatırımcılara "
    "gerçekleştirilecek satışlar için tahsis edilmiştir."
)


def make_fake_run(per_page_text: dict[str, str]):
    calls: list[list[str]] = []

    def _fake_run(args, *, capture_output=True, timeout=None, **kwargs):
        calls.append(list(args))
        if args[:2] == ["tesseract", "--version"]:
            return _FakeCompletedProcess(returncode=0, stdout=b"tesseract 5.5.1\n leptonica-1.86.0")
        image_path = Path(args[1])
        output_base = Path(args[2])
        text = per_page_text.get(image_path.stem, "sayfa metni yok")
        output_base.with_suffix(".txt").write_text(text, encoding="utf-8")
        return _FakeCompletedProcess(returncode=0)

    _fake_run.calls = calls
    return _fake_run


def _build_pdf_bytes(num_pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _prospectus_disclosure(*, disclosure_id: str, obj_id: str, record_id: str, summary: str) -> KapDisclosure:
    attachment = KapAttachment(
        name="izahname.pdf", url=f"https://example/{obj_id}", content_type="application/pdf",
        document_role="primary", obj_id=obj_id,
    )
    return KapDisclosure(
        disclosure_id=disclosure_id,
        disclosure_index=1,
        published_at=datetime(2026, 1, 1),
        company_name="Örnek A.Ş.",
        ticker="ORNK",
        title="İzahname (SPK Tarafından Onaylanan)",
        summary=summary,
        document_type="approved_prospectus",
        notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(attachment.url,),
        matched_spk_record_id=record_id,
        match_method="ticker",
        raw={},
        attachments=(attachment,),
        primary_document=attachment,
        pdf_status="scanned",
    )


def test_recover_allocation_sections_finds_table_beyond_default_ocr_cap(monkeypatch, tmp_path):
    # A 4-page scanned base prospectus whose tahsisat table only sits on
    # page 4 — past a normal OCR_MAX_PAGES=2 budget used for this test.
    pdf_bytes = _build_pdf_bytes(num_pages=4)
    per_page_text = {
        "page-1": "Kapak sayfası.",
        "page-2": "İçindekiler.",
        "page-3": "Halka arza ilişkin genel bilgiler.",
        "page-4": ALLOCATION_TABLE_TEXT,
    }
    monkeypatch.setattr(ocr_module.subprocess, "run", make_fake_run(per_page_text))

    pdf_cache = PdfCache(tmp_path / "kap_pdfs")
    ocr_cache = OcrCache(tmp_path / "kap_ocr")
    pdf_cache.put("obj-1", pdf_bytes)
    disclosure = _prospectus_disclosure(
        disclosure_id="d-1", obj_id="obj-1", record_id="ipo:ORNK:2026", summary="İzahname 1. Bölüm"
    )

    ocr_config = OcrConfig(dpi=72, max_pages=2, timeout_seconds=10, languages="tur+eng")

    result = recover_allocation_sections(
        "ipo:ORNK:2026",
        [disclosure],
        pdf_cache=pdf_cache,
        ocr_cache=ocr_cache,
        ocr_config=ocr_config,
        page_step=2,
        max_deep_pages=6,
    )

    assert result.already_resolved is False
    assert result.resolved is True
    assert result.offering_terms.investor_group_allocations.status == "extracted"
    assert result.offering_terms.retail_allocation_percentage.value == 40.0
    assert result.offering_terms.retail_allocation_percentage.status == "extracted"
    assert result.attempts  # at least one deep-OCR step was recorded
    assert result.attempts[-1].resolved_after is True
    assert result.attempts[-1].pages_ocrd == 4

    # A repeat pass over the already-updated disclosures does no new OCR
    # work at all — the fields are already resolved from the first pass.
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("repeat run should need no further OCR work")

    monkeypatch.setattr(ocr_module.subprocess, "run", _fail_if_called)
    repeat = recover_allocation_sections(
        "ipo:ORNK:2026",
        list(result.updated_disclosures),
        pdf_cache=pdf_cache,
        ocr_cache=ocr_cache,
        ocr_config=ocr_config,
        page_step=2,
        max_deep_pages=6,
    )
    assert repeat.already_resolved is True
    assert repeat.resolved is True


def test_recover_allocation_sections_skips_digitally_readable_prospectus(tmp_path):
    # pdf_status="ok" (a normal digital PDF) is never OCR'd at all (see
    # kap.ocr's module docstring) — a missing field there is an
    # extraction-pattern gap, not an OCR page-budget one, so this
    # fallback must not attempt anything (and needs no cached PDF bytes).
    pdf_cache = PdfCache(tmp_path / "kap_pdfs")
    ocr_cache = OcrCache(tmp_path / "kap_ocr")
    disclosure = _prospectus_disclosure(
        disclosure_id="d-1", obj_id="obj-1", record_id="ipo:ORNK:2026", summary="İzahname 1. Bölüm"
    )
    disclosure = replace(disclosure, pdf_status="ok")

    result = recover_allocation_sections(
        "ipo:ORNK:2026", [disclosure], pdf_cache=pdf_cache, ocr_cache=ocr_cache, page_step=2, max_deep_pages=6
    )

    assert result.already_resolved is False
    assert result.resolved is False
    assert result.attempts == ()
