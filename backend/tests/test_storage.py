import io
import zipfile

import pytest
from fastapi import UploadFile

from app.documents.storage import document_dir, read_upload, save_upload_stream
from app.documents.validation import (
    MAGIC_SNIFF_BYTES,
    UploadTooLarge,
    UploadValidationError,
    extension_of,
    validate_magic_bytes,
    validate_upload_metadata,
)

PDF_HEAD = b"%PDF-1.4\n" + b"0" * 300
DOCX_HEAD = b"PK\x03\x04" + b"0" * 300


def _real_docx() -> bytes:
    """A structurally valid OOXML package - `filetype` only reports the docx mime
    (rather than the plain zip container) when it can read the whole archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr("_rels/.rels", "<?xml version='1.0'?><Relationships/>")
        archive.writestr("word/document.xml", "<?xml version='1.0'?><w:document/>")
    return buf.getvalue()


def _upload(name: str, data: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        filename=name, file=io.BytesIO(data), headers={"content-type": content_type}
    )


async def test_save_upload_stream_round_trip(tmp_path):
    upload = _upload("report.pdf", PDF_HEAD, "application/pdf")
    path, size = await save_upload_stream(tmp_path, "doc-1", "pdf", upload, max_bytes=4096)

    assert path == tmp_path / "doc-1" / "source.pdf"
    assert size == len(PDF_HEAD)
    assert await read_upload(path) == PDF_HEAD


@pytest.mark.parametrize(
    "evil_name",
    [
        "../../evil.txt",
        "..\\..\\evil.txt",  # Windows separator: ntpath treats this as traversal too
        "../../../../evil.pdf",
        "/etc/passwd.pdf",
    ],
)
async def test_storage_path_ignores_the_client_filename(tmp_path, evil_name):
    """A traversal filename must not influence the path at all: the server names
    the file from the validated extension."""
    upload = _upload(evil_name, PDF_HEAD, "application/pdf")
    path, _ = await save_upload_stream(tmp_path, "doc-2", "pdf", upload, max_bytes=4096)

    assert path.resolve().is_relative_to(tmp_path.resolve())
    assert path.name == "source.pdf"
    assert path.parent == document_dir(tmp_path, "doc-2")
    # Nothing was created outside the upload root.
    assert not (tmp_path.parent / "evil.txt").exists()


async def test_oversized_upload_is_rejected_while_streaming(tmp_path):
    upload = _upload("big.pdf", b"x" * 5000, "application/pdf")
    with pytest.raises(UploadTooLarge):
        await save_upload_stream(tmp_path, "doc-3", "pdf", upload, max_bytes=1000)
    # Partial output must not survive a rejected upload.
    assert not (tmp_path / "doc-3" / "source.pdf").exists()


async def test_oversize_is_detected_before_the_whole_body_is_consumed(tmp_path):
    """Proves the limit is enforced *during* the stream: the source is left with
    unread bytes, so the rejection cannot have required reading it all."""
    from app.documents.storage import CHUNK_BYTES

    source = io.BytesIO(b"x" * (CHUNK_BYTES * 3))
    upload = UploadFile(
        filename="big.pdf", file=source, headers={"content-type": "application/pdf"}
    )
    with pytest.raises(UploadTooLarge):
        await save_upload_stream(tmp_path, "doc-4", "pdf", upload, max_bytes=10)

    assert source.tell() == CHUNK_BYTES
    assert not document_dir(tmp_path, "doc-4").exists()


def test_extension_of():
    assert extension_of("Report.FINAL.PDF") == "pdf"
    assert extension_of("noextension") == ""


def test_validate_upload_metadata_accepts_an_allowed_file():
    assert validate_upload_metadata("report.pdf", "application/pdf", 1000, 50) == "pdf"


def test_validate_upload_metadata_rejects_a_bad_extension():
    with pytest.raises(UploadValidationError):
        validate_upload_metadata("virus.exe", "application/octet-stream", 1000, 50)


def test_validate_upload_metadata_rejects_a_mismatched_content_type():
    with pytest.raises(UploadValidationError):
        validate_upload_metadata("report.pdf", "text/html", 1000, 50)


def test_validate_upload_metadata_rejects_a_declared_oversize():
    with pytest.raises(UploadTooLarge):
        validate_upload_metadata("report.pdf", "application/pdf", 100 * 1024 * 1024, 50)


def test_validate_magic_bytes_accepts_matching_content():
    validate_magic_bytes("pdf", PDF_HEAD)
    validate_magic_bytes("docx", DOCX_HEAD)
    validate_magic_bytes("txt", "안녕하세요".encode())


def test_validate_magic_bytes_accepts_a_real_docx_at_any_sniff_length():
    """`filetype` reports application/zip from a truncated head but the full OOXML
    mime once it can read the archive's central directory. Both must be accepted:
    keying on application/zip alone rejects every real .docx the moment a caller
    hands over more than MAGIC_SNIFF_BYTES."""
    docx = _real_docx()
    validate_magic_bytes("docx", docx[:MAGIC_SNIFF_BYTES])
    validate_magic_bytes("docx", docx)


def test_validate_magic_bytes_rejects_a_renamed_html_file():
    with pytest.raises(UploadValidationError):
        validate_magic_bytes("pdf", b"<html><body>not a pdf</body></html>")


def test_validate_magic_bytes_rejects_an_executable_renamed_to_txt():
    with pytest.raises(UploadValidationError):
        validate_magic_bytes("txt", b"MZ\x90\x00" + b"\x00" * 300)
