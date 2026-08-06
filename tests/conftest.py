import io
import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html():
    def _load(name: str) -> str:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")

    return _load


@pytest.fixture
def fixture_json():
    def _load(name: str):
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return _load


def _build_pdf_bytes(*, text: str | None = None, with_image: bool = False) -> bytes:
    """Build a small, real, valid single-page PDF for tests — with a real
    text content stream, a fake (undecoded) image XObject, or neither."""
    from pypdf import PdfWriter
    from pypdf.generic import ContentStream, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    resources = DictionaryObject()

    if text is not None:
        font = DictionaryObject()
        font[NameObject("/Type")] = NameObject("/Font")
        font[NameObject("/Subtype")] = NameObject("/Type1")
        font[NameObject("/BaseFont")] = NameObject("/Helvetica")
        font_dict = DictionaryObject()
        font_dict[NameObject("/F1")] = writer._add_object(font)
        resources[NameObject("/Font")] = font_dict

        content = ContentStream(None, writer)
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content.set_data(f"BT /F1 12 Tf 10 100 Td ({escaped}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(content)

    if with_image:
        image = DictionaryObject()
        image[NameObject("/Type")] = NameObject("/XObject")
        image[NameObject("/Subtype")] = NameObject("/Image")
        image[NameObject("/Width")] = NumberObject(10)
        image[NameObject("/Height")] = NumberObject(10)
        image[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
        image[NameObject("/BitsPerComponent")] = NumberObject(8)
        image._data = b"\x00" * 100
        xobject_dict = DictionaryObject()
        xobject_dict[NameObject("/Im0")] = writer._add_object(image)
        resources[NameObject("/XObject")] = xobject_dict

    page[NameObject("/Resources")] = resources

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def build_pdf_bytes():
    return _build_pdf_bytes
