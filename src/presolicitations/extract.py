"""Text extraction. Scanned PDFs come back empty and are flagged by the caller, never OCR'd."""
from __future__ import annotations

import html
import io
import re
import subprocess
import zipfile


def clean_docx_xml(xml: str) -> str:
    """Tags to spaces, XML entities decoded once, whitespace collapsed.

    Regression: the first pull skipped the decode, so 133 of 346 DOCX texts held `&amp;`, `&#8217;`
    and the like, and searching for "&" or a quote missed them. Word field codes such as a table of
    contents (`TOC \\o "1-4"`) are double-encoded and keep a literal `&quot;` after one pass; that is
    field-instruction noise, left as is.
    """
    text = re.sub(r"<[^>]+>", " ", xml)
    return " ".join(html.unescape(text).split())


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return clean_docx_xml(z.read("word/document.xml").decode("utf-8", "replace"))


def pdf_text(data: bytes) -> str:
    p = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, timeout=120
    )
    if p.returncode != 0:
        raise RuntimeError(f"pdftotext exit {p.returncode}: {p.stderr[:200]!r}")
    return p.stdout.decode("utf-8", "replace")


def ext_of(name: str) -> str:
    return name.lower().rsplit(".", 1)[-1] if "." in name else ""


def extract(name: str, data: bytes) -> str:
    ext = ext_of(name)
    if ext == "pdf":
        return pdf_text(data)
    if ext == "docx":
        return docx_text(data)
    raise ValueError(f"no extractor for .{ext}")
