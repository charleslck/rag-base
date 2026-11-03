"""Document parsing and chunking.

Supported formats: .txt .md .markdown .pdf .docx .html
Chunking is character-based with paragraph/sentence-boundary preference and
overlap, which works well for both English and Chinese text.
"""
import re

from .config import settings


class ParseError(Exception):
    pass


def extract_text(filename: str, raw: bytes) -> str:
    lower = filename.lower()
    try:
        if lower.endswith((".txt", ".md", ".markdown")):
            return raw.decode("utf-8", errors="replace")
        if lower.endswith(".pdf"):
            from pypdf import PdfReader
            import io

            reader = PdfReader(io.BytesIO(raw))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        if lower.endswith(".docx"):
            import io
            from docx import Document

            doc = Document(io.BytesIO(raw))
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if lower.endswith((".html", ".htm")):
            text = raw.decode("utf-8", errors="replace")
            text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            return re.sub(r"\s+", " ", text)
    except Exception as exc:  # corrupt file etc.
        raise ParseError(f"Failed to parse {filename}: {exc}") from exc
    raise ParseError(f"Unsupported file type: {filename} (use PDF, DOCX, TXT, MD or HTML)")


def chunk_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    size = chunk_size or settings.chunk_size
    ov = min(overlap if overlap is not None else settings.chunk_overlap, size // 3)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    # prefer splitting at double newlines, then sentence ends, then hard cut
    chunks, start = [], 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("。"), window.rfind("! "), window.rfind("? "))
            if cut > size * 0.4:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - ov, start + 1)
    return chunks
