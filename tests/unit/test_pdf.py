import pytest

from common.pdf import extract_pdf_text


def _build_minimal_pdf(*page_texts: str) -> bytes:
    """Hand-build a minimal valid PDF with one page per string in `page_texts`,
    each page containing that text as a single Tj show-text operator. No
    pypdf.PdfWriter involved (it can't draw text) and no test-only dependency
    like reportlab -- just enough of the PDF object model for pypdf to parse
    back out via extract_text()."""
    num_pages = len(page_texts)
    page_ids = list(range(3, 3 + num_pages))
    font_id = 3 + num_pages
    content_ids = list(range(font_id + 1, font_id + 1 + num_pages))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {num_pages} >>".encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for pid, cid in zip(page_ids, content_ids, strict=True):
        objects[pid] = (
            f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/MediaBox [0 0 200 200] /Contents {cid} 0 R >>"
        ).encode()
    for cid, text in zip(content_ids, page_texts, strict=True):
        stream = f"BT /F1 24 Tf 72 100 Td ({text}) Tj ET".encode()
        objects[cid] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for obj_id in sorted(objects):
        offsets[obj_id] = len(out)
        out += f"{obj_id} 0 obj\n".encode() + objects[obj_id] + b"\nendobj\n"

    xref_offset = len(out)
    max_id = max(objects)
    out += f"xref\n0 {max_id + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for obj_id in range(1, max_id + 1):
        out += f"{offsets[obj_id]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_offset}\n%%EOF".encode()
    return bytes(out)


def test_extracts_single_page_text():
    pdf = _build_minimal_pdf("Hello World")
    assert "Hello World" in extract_pdf_text(pdf)


def test_joins_multiple_pages():
    pdf = _build_minimal_pdf("Page One", "Page Two")
    text = extract_pdf_text(pdf)
    assert "Page One" in text
    assert "Page Two" in text
    assert text.index("Page One") < text.index("Page Two")


def test_raises_on_encrypted_pdf():
    pdf = _build_minimal_pdf("secret")

    class _FakeEncryptedReader:
        is_encrypted = True

    import common.pdf as pdf_module

    original_reader = pdf_module.PdfReader
    pdf_module.PdfReader = lambda _data: _FakeEncryptedReader()
    try:
        with pytest.raises(ValueError, match="encrypted"):
            extract_pdf_text(pdf)
    finally:
        pdf_module.PdfReader = original_reader
