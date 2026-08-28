import shutil
from pathlib import Path

from anyio import to_thread
from fastapi import UploadFile

from app.documents.validation import UploadTooLarge

CHUNK_BYTES = 1024 * 1024


def document_dir(upload_dir: Path, document_id: str) -> Path:
    return Path(upload_dir) / document_id


def storage_path(upload_dir: Path, document_id: str, extension: str) -> Path:
    # The client-supplied filename NEVER contributes to the path. It is kept in
    # documents.filename for display only.
    return document_dir(upload_dir, document_id) / f"source.{extension}"


async def save_upload_stream(
    upload_dir: Path,
    document_id: str,
    extension: str,
    upload: UploadFile,
    max_bytes: int,
) -> tuple[Path, int]:
    """Stream to disk in 1MB pieces, aborting the moment the running total passes
    max_bytes. Reading the whole body into memory first turns a 5GB POST into an
    OOM kill before any size check can run."""
    target = storage_path(upload_dir, document_id, extension)
    await to_thread.run_sync(lambda: target.parent.mkdir(parents=True, exist_ok=True))

    total = 0
    handle = await to_thread.run_sync(lambda: target.open("wb"))
    try:
        while True:
            piece = await upload.read(CHUNK_BYTES)
            if not piece:
                break
            total += len(piece)
            if total > max_bytes:
                raise UploadTooLarge(f"upload exceeds {max_bytes} bytes")
            await to_thread.run_sync(handle.write, piece)
    except BaseException:
        await to_thread.run_sync(handle.close)
        await to_thread.run_sync(lambda: shutil.rmtree(target.parent, ignore_errors=True))
        raise
    else:
        await to_thread.run_sync(handle.close)

    return target, total


async def read_upload(path: Path | str) -> bytes:
    # Blocking read moved off the event loop: a 50MB read on the API loop stalls
    # in-flight chat requests, which the requirements explicitly forbid.
    return await to_thread.run_sync(Path(path).read_bytes)


async def delete_document_files(upload_dir: Path, document_id: str) -> None:
    directory = document_dir(upload_dir, document_id)
    await to_thread.run_sync(lambda: shutil.rmtree(directory, ignore_errors=True))
