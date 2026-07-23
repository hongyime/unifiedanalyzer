from pathlib import Path

from src.pipeline import media_analysis


def test_looks_like_pdf_rejects_html_error_page(tmp_path: Path):
    path = tmp_path / "not-a-real.pdf"
    path.write_bytes(b"<!DOCTYPE html><title>rate limited</title>")

    assert media_analysis._looks_like_pdf(path) is False


def test_looks_like_pdf_accepts_pdf_header_with_leading_whitespace(tmp_path: Path):
    path = tmp_path / "real.pdf"
    path.write_bytes(b"\n  %PDF-1.7\n")

    assert media_analysis._looks_like_pdf(path) is True


def test_extract_pdf_text_skips_non_pdf_without_invoking_parser(monkeypatch, tmp_path: Path):
    path = tmp_path / "not-a-real.pdf"
    path.write_bytes(b"<!DOCTYPE html><title>rate limited</title>")

    def fail_if_called(_path):
        raise AssertionError("PdfReader should not be called for a non-PDF header")

    monkeypatch.setattr(media_analysis.pypdf, "PdfReader", fail_if_called)

    assert media_analysis._extract_pdf_text(path) == ""
