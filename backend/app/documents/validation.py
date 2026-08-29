import filetype

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md", "html"}

# Browsers are inconsistent about text/* types, so each extension carries a small
# allowlist rather than a single expected value.
ALLOWED_CONTENT_TYPES = {
    "pdf": {"application/pdf"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    "txt": {"text/plain", "application/octet-stream"},
    "md": {"text/markdown", "text/plain", "text/x-markdown", "application/octet-stream"},
    "html": {"text/html", "application/xhtml+xml", "text/plain"},
}

# Expected sniffed mimes for binary formats. Text formats are checked by ruling
# OUT binary signatures instead, because plain text has no magic bytes.
EXPECTED_MAGIC_MIME = {
    "pdf": {"application/pdf"},
    # A .docx IS a zip, and which mime `filetype` reports depends entirely on how
    # many bytes it was given: the plain container from a truncated head, the real
    # OOXML type once it can read the archive's central directory. Accepting only
    # application/zip rejects every real .docx the moment a caller sniffs more than
    # MAGIC_SNIFF_BYTES, so both are listed.
    "docx": {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
}
TEXT_EXTENSIONS = {"txt", "md", "html"}
MAGIC_SNIFF_BYTES = 261  # what `filetype` needs to identify every supported format


class UploadValidationError(ValueError):
    pass


class UploadTooLarge(UploadValidationError):
    pass


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def validate_upload_metadata(filename: str, content_type: str, declared_size: int, max_size_mb: int) -> str:
    extension = extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(f"지원하지 않는 파일 형식입니다: .{extension}")

    normalised = (content_type or "").split(";", 1)[0].strip().lower()
    if normalised and normalised not in ALLOWED_CONTENT_TYPES[extension]:
        raise UploadValidationError(f"Content-Type {normalised}은(는) .{extension} 파일과 맞지 않습니다.")

    if declared_size > max_size_mb * 1024 * 1024:
        raise UploadTooLarge(f"파일이 최대 크기 {max_size_mb}MB를 초과했습니다.")

    return extension


def validate_magic_bytes(extension: str, head: bytes) -> None:
    """Third check, after extension and Content-Type: a .pdf-named ZIP bomb or an
    HTML file passes both of those. `filetype` is pure Python - python-magic would
    need the libmagic DLL and break the Windows/Linux parity requirement."""
    guess = filetype.guess(head)

    if extension in TEXT_EXTENSIONS:
        if guess is not None:
            raise UploadValidationError(f".{extension} 업로드에 바이너리 내용({guess.mime})이 들어 있습니다.")
        if b"\x00" in head:
            raise UploadValidationError(f".{extension} 업로드에 바이너리 내용이 들어 있습니다.")
        return

    expected = EXPECTED_MAGIC_MIME[extension]
    if guess is None or guess.mime not in expected:
        actual = guess.mime if guess else "unknown"
        raise UploadValidationError(f"파일 내용({actual})이 .{extension} 확장자와 맞지 않습니다.")
