"""Document parsing and chunking."""
from __future__ import annotations

import pytest

from app import ingest
from helpers import make_docx, make_pdf


# --------------------------------------------------------------------- text

def test_txt_is_decoded_as_utf8():
    assert ingest.extract_text("notes.txt", "héllo wörld".encode()) == "héllo wörld"


@pytest.mark.parametrize("name", ["readme.md", "readme.MARKDOWN", "notes.TXT"])
def test_markdown_and_uppercase_extensions_are_text(name):
    assert ingest.extract_text(name, b"# Title\n\nBody") == "# Title\n\nBody"


def test_invalid_utf8_is_replaced_not_fatal():
    text = ingest.extract_text("broken.txt", b"ok \xff\xfe bytes")
    assert text.startswith("ok ")
    assert "\ufffd" in text


def test_html_strips_scripts_styles_and_tags():
    raw = (
        b"<html><head><style>body{color:red}</style><script>var secret=1;</script></head>"
        b"<body><h1>Refunds</h1><p>Return   within 30 days.</p></body></html>"
    )
    text = ingest.extract_text("page.html", raw)
    assert "color:red" not in text
    assert "secret" not in text
    assert "Refunds" in text
    assert "Return within 30 days." in text
    assert "<" not in text and ">" not in text


def test_htm_extension_is_supported():
    assert "Body" in ingest.extract_text("page.htm", b"<p>Body</p>")


def test_docx_paragraphs_are_joined_and_blanks_skipped():
    data = make_docx(["First paragraph.", "", "   ", "Second paragraph."])
    assert ingest.extract_text("guide.docx", data) == "First paragraph.\n\nSecond paragraph."


def test_pdf_text_is_extracted():
    data = make_pdf("Refund policy inside a PDF.")
    assert "Refund policy inside a PDF." in ingest.extract_text("guide.pdf", data)


def test_corrupt_pdf_raises_parse_error():
    with pytest.raises(ingest.ParseError) as excinfo:
        ingest.extract_text("broken.pdf", b"definitely not a pdf")
    assert "Failed to parse broken.pdf" in str(excinfo.value)


def test_corrupt_docx_raises_parse_error():
    with pytest.raises(ingest.ParseError) as excinfo:
        ingest.extract_text("broken.docx", b"PK\x03\x04 nonsense")
    assert "Failed to parse broken.docx" in str(excinfo.value)


def test_unsupported_extension_raises_parse_error():
    with pytest.raises(ingest.ParseError) as excinfo:
        ingest.extract_text("payload.exe", b"MZ")
    assert "Unsupported file type" in str(excinfo.value)
    assert "payload.exe" in str(excinfo.value)


# ------------------------------------------------------------------- chunking

def test_empty_and_whitespace_text_produce_no_chunks():
    assert ingest.chunk_text("") == []
    assert ingest.chunk_text("   \n\n\t  \n") == []


def test_short_text_is_a_single_chunk():
    assert ingest.chunk_text("A very short note.") == ["A very short note."]


def test_collapses_excess_blank_lines():
    chunks = ingest.chunk_text("a\n\n\n\n\nb", chunk_size=1000)
    assert chunks == ["a\n\nb"]


def test_chunks_never_exceed_the_requested_size():
    text = " ".join(f"Word{i} appears in this sentence." for i in range(400))
    chunks = ingest.chunk_text(text, chunk_size=200, overlap=40)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_consecutive_chunks_overlap():
    text = " ".join(f"Sentence {i} talks about topic {i}." for i in range(200))
    size, overlap = 300, 60
    chunks = ingest.chunk_text(text, chunk_size=size, overlap=overlap)

    assert len(chunks) > 2
    for previous, following in zip(chunks, chunks[1:]):
        shared = 0
        # the tail of the previous chunk must reappear at the head of the next
        for length in range(min(overlap, len(previous), len(following)), 0, -1):
            if previous[-length:] == following[:length]:
                shared = length
                break
        assert shared > 0


def test_chunks_prefer_sentence_boundaries():
    text = "A" * 60 + ". " + "B" * 60 + ". " + "C" * 60 + "."
    chunks = ingest.chunk_text(text, chunk_size=80, overlap=10)
    # first chunk should stop right after the first full stop rather than mid-word
    assert chunks[0].endswith(".")


def test_overlap_is_capped_at_one_third_of_the_chunk_size():
    text = " ".join(f"Token{i}." for i in range(300))
    chunks = ingest.chunk_text(text, chunk_size=60, overlap=10_000)
    # 10_000 would exceed the chunk length; the cap keeps the loop progressing
    assert len(chunks) > 1
    assert all(len(c) <= 60 for c in chunks)


def test_chinese_text_is_chunked_on_full_stops():
    text = "退款政策允许在购买后30天内退货。" * 30
    chunks = ingest.chunk_text(text, chunk_size=120, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 120 for c in chunks)
    assert chunks[0].endswith("。") or len(chunks[0]) == 120


def test_chunking_is_deterministic():
    text = " ".join(f"Item {i} description." for i in range(100))
    assert ingest.chunk_text(text, 200, 30) == ingest.chunk_text(text, 200, 30)


def test_empty_chunks_from_punctuation_only_text_are_dropped():
    # punctuation-only content still yields a chunk, but whitespace-only does not
    assert ingest.chunk_text("... ...") == ["... ..."]
    assert ingest.chunk_text("\n\n\n") == []
