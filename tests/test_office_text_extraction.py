import importlib.util

import pytest

from src.pipeline import media_analysis as ma

_HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None


def test_office_text_available_returns_bool():
    assert isinstance(ma.office_text_available(), bool)


def test_extract_office_text_graceful_on_unknown_kind(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"not a real office file")
    # Unknown kind or missing lib must return "" (never raise).
    assert ma._extract_office_text(p, "bin") == ""
    assert ma._extract_office_text(p, "docx") == ""  # returns "" if lib absent OR parse fails


def test_extract_office_text_graceful_on_corrupt_file(tmp_path):
    p = tmp_path / "bad.xlsx"
    p.write_bytes(b"\x00\x01 not a zip")
    assert ma._extract_office_text(p, "xlsx") == ""


@pytest.mark.skipif(not _HAS_OPENPYXL, reason="openpyxl not installed in this env")
def test_extract_xlsx_real(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["alice@example.com", "evil-domain.test"])
    ws.append(["hello world", 42])
    p = tmp_path / "sheet.xlsx"
    wb.save(str(p))

    text = ma._extract_office_text(p, "xlsx")
    assert "alice@example.com" in text
    assert "evil-domain.test" in text
    assert "hello world" in text
