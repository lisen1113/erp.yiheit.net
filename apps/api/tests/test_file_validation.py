from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from yihe_api.files.validation import Kind, Verdict, filename_extension, scan, verify_format


@pytest.mark.parametrize("filename", ["../x.png", "x.exe.png", "x.svg", "x\\y.png"])
def test_unsafe_names_or_svg_are_rejected(filename: str) -> None:
    with pytest.raises(ValueError):
        filename_extension(filename, Kind.BRAND, "image/png")


@pytest.mark.parametrize("extension", ["jpg", "pdf"])
def test_executable_magic_rejected(extension: str) -> None:
    with pytest.raises(ValueError, match="Executable"):
        verify_format(b"MZ-synthetic", extension)


@pytest.mark.parametrize("unsafe_member", ["xl/vbaProject.bin", "../outside", "xl/bomb.xml"])
def test_office_package_constraints(unsafe_member: str) -> None:
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr(unsafe_member, "a" * 200000)
    with pytest.raises(ValueError):
        verify_format(stream.getvalue(), "xlsx")


def test_no_scanner_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ERP_CLAMD_HOST", raising=False)
    assert scan(b"example") == Verdict.UNAVAILABLE
