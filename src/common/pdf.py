"""PDF text extraction: pure-Python via pypdf, no OCR -- extracts embedded
text objects only. Scanned/image-only PDFs yield an empty string, which the
dispatcher's .pdf branch treats as a hard failure (see dispatcher/handler.py).
"""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_pdf_text(data: bytes) -> str:
    """Extract reading-order text from a PDF's raw bytes, pages joined by blank lines.

    Raises ValueError if the PDF is encrypted -- support would need the optional
    pypdf[crypto] extra, which pulls in the compiled `cryptography` package,
    deliberately not added as a dependency. A single page that fails to parse is
    logged and skipped rather than failing the whole document.
    """
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("encrypted PDFs are not supported")

    pages_text: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:
            logger.warning("failed to extract text from page %d, skipping", i, exc_info=True)

    return "\n\n".join(t for t in pages_text if t)
