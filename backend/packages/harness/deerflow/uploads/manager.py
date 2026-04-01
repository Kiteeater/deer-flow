"""Shared upload management logic.

Pure business logic — no FastAPI/HTTP dependencies.
Both Gateway and Client delegate to these functions.
"""

import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths


class PathTraversalError(ValueError):
    """Raised when a path escapes its allowed base directory."""


# thread_id must be alphanumeric, hyphens, underscores, or dots only.
_SAFE_THREAD_ID = re.compile(r"^[a-zA-Z0-9._-]+$")
_DOCX_SIDECAR_MANIFEST_SUFFIX = ".docx.images.json"


def validate_thread_id(thread_id: str) -> None:
    """Reject thread IDs containing characters unsafe for filesystem paths.

    Raises:
        ValueError: If thread_id is empty or contains unsafe characters.
    """
    if not thread_id or not _SAFE_THREAD_ID.match(thread_id):
        raise ValueError(f"Invalid thread_id: {thread_id!r}")


def get_uploads_dir(thread_id: str) -> Path:
    """Return the uploads directory path for a thread (no side effects)."""
    validate_thread_id(thread_id)
    return get_paths().sandbox_uploads_dir(thread_id)


def ensure_uploads_dir(thread_id: str) -> Path:
    """Return the uploads directory for a thread, creating it if needed."""
    base = get_uploads_dir(thread_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def normalize_filename(filename: str) -> str:
    """Sanitize a filename by extracting its basename.

    Strips any directory components and rejects traversal patterns.

    Args:
        filename: Raw filename from user input (may contain path components).

    Returns:
        Safe filename (basename only).

    Raises:
        ValueError: If filename is empty or resolves to a traversal pattern.
    """
    if not filename:
        raise ValueError("Filename is empty")
    safe = Path(filename).name
    if not safe or safe in {".", ".."}:
        raise ValueError(f"Filename is unsafe: {filename!r}")
    # Reject backslashes — on Linux Path.name keeps them as literal chars,
    # but they indicate a Windows-style path that should be stripped or rejected.
    if "\\" in safe:
        raise ValueError(f"Filename contains backslash: {filename!r}")
    if len(safe.encode("utf-8")) > 255:
        raise ValueError(f"Filename too long: {len(safe)} chars")
    return safe


def claim_unique_filename(name: str, seen: set[str]) -> str:
    """Generate a unique filename by appending ``_N`` suffix on collision.

    Automatically adds the returned name to *seen* so callers don't need to.

    Args:
        name: Candidate filename.
        seen: Set of filenames already claimed (mutated in place).

    Returns:
        A filename not present in *seen* (already added to *seen*).
    """
    if name not in seen:
        seen.add(name)
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    candidate = f"{stem}_{counter}{suffix}"
    while candidate in seen:
        counter += 1
        candidate = f"{stem}_{counter}{suffix}"
    seen.add(candidate)
    return candidate


def docx_sidecar_manifest_filename(filename: str) -> str:
    """Return the manifest filename for a docx source filename."""
    return f"{filename}{_DOCX_SIDECAR_MANIFEST_SUFFIX}"


def docx_sidecar_manifest_path(file_path: Path) -> Path:
    """Return the manifest path for a docx source path."""
    return file_path.with_name(docx_sidecar_manifest_filename(file_path.name))


def write_docx_sidecar_manifest(file_path: Path, image_paths: list[Path]) -> None:
    """Persist the extracted sidecar-image mapping for a docx upload."""
    manifest_path = docx_sidecar_manifest_path(file_path)
    if not image_paths:
        manifest_path.unlink(missing_ok=True)
        return

    manifest_path.write_text(
        json.dumps(
            {
                "source_filename": file_path.name,
                "image_filenames": [image_path.name for image_path in image_paths],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def read_docx_sidecar_manifest(base_dir: Path, filename: str) -> list[str]:
    """Return the manifest-declared sidecar filenames for a docx upload."""
    manifest_path = base_dir / docx_sidecar_manifest_filename(filename)
    if not manifest_path.is_file():
        return []

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if data.get("source_filename") != filename:
        return []

    image_filenames = data.get("image_filenames")
    if not isinstance(image_filenames, list):
        return []

    sanitized: list[str] = []
    for image_filename in image_filenames:
        if not isinstance(image_filename, str):
            continue
        if not image_filename or Path(image_filename).name != image_filename:
            continue
        sanitized.append(image_filename)
    return sanitized


def delete_docx_sidecars(file_path: Path) -> None:
    """Delete manifest-managed docx sidecar images for a source file."""
    base_dir = file_path.parent
    for image_filename in read_docx_sidecar_manifest(base_dir, file_path.name):
        (base_dir / image_filename).unlink(missing_ok=True)
    docx_sidecar_manifest_path(file_path).unlink(missing_ok=True)


def validate_path_traversal(path: Path, base: Path) -> None:
    """Verify that *path* is inside *base*.

    Raises:
        PathTraversalError: If a path traversal is detected.
    """
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise PathTraversalError("Path traversal detected") from None


def list_files_in_dir(directory: Path) -> dict:
    """List files (not directories) in *directory*.

    Args:
        directory: Directory to scan.

    Returns:
        Dict with "files" list (sorted by name) and "count".
        Each file entry has ``size`` as *int* (bytes).  Call
        :func:`enrich_file_listing` to stringify sizes and add
        virtual / artifact URLs.
    """
    if not directory.is_dir():
        return {"files": [], "count": 0}

    files = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda e: e.name):
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.name.endswith(_DOCX_SIDECAR_MANIFEST_SUFFIX):
                continue
            st = entry.stat(follow_symlinks=False)
            files.append(
                {
                    "filename": entry.name,
                    "size": st.st_size,
                    "path": entry.path,
                    "extension": Path(entry.name).suffix,
                    "modified": st.st_mtime,
                }
            )
    return {"files": files, "count": len(files)}


def delete_file_safe(base_dir: Path, filename: str, *, convertible_extensions: set[str] | None = None) -> dict:
    """Delete a file inside *base_dir* after path-traversal validation.

    If *convertible_extensions* is provided and the file's extension matches,
    the companion ``.md`` file is also removed (if it exists).

    Args:
        base_dir: Directory containing the file.
        filename: Name of file to delete.
        convertible_extensions: Lowercase extensions (e.g. ``{".pdf", ".docx"}``)
            whose companion markdown should be cleaned up.

    Returns:
        Dict with success and message.

    Raises:
        FileNotFoundError: If the file does not exist.
        PathTraversalError: If path traversal is detected.
    """
    file_path = (base_dir / filename).resolve()
    validate_path_traversal(file_path, base_dir)

    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {filename}")

    file_path.unlink()

    # Clean up companion markdown generated during upload conversion.
    if convertible_extensions and file_path.suffix.lower() in convertible_extensions:
        file_path.with_suffix(".md").unlink(missing_ok=True)
        delete_docx_sidecars(file_path)

    return {"success": True, "message": f"Deleted {filename}"}


def upload_artifact_url(thread_id: str, filename: str) -> str:
    """Build the artifact URL for a file in a thread's uploads directory.

    *filename* is percent-encoded so that spaces, ``#``, ``?`` etc. are safe.
    """
    return f"/api/threads/{thread_id}/artifacts{VIRTUAL_PATH_PREFIX}/uploads/{quote(filename, safe='')}"


def upload_virtual_path(filename: str) -> str:
    """Build the virtual path for a file in the uploads directory."""
    return f"{VIRTUAL_PATH_PREFIX}/uploads/{filename}"


def enrich_file_listing(result: dict, thread_id: str) -> dict:
    """Add virtual paths, artifact URLs, and stringify sizes on a listing result.

    Mutates *result* in place and returns it for convenience.
    """
    extracted_image_filenames: set[str] = set()
    for f in result["files"]:
        filename = f["filename"]
        if Path(filename).suffix.lower() != ".docx":
            continue
        for image_filename in read_docx_sidecar_manifest(Path(f["path"]).parent, filename):
            image_path = Path(f["path"]).parent / image_filename
            if image_path.is_file():
                extracted_image_filenames.add(image_filename)

    enriched_files: list[dict] = []
    for f in result["files"]:
        filename = f["filename"]
        if filename in extracted_image_filenames:
            continue

        f["size"] = str(f["size"])
        f["virtual_path"] = upload_virtual_path(filename)
        f["artifact_url"] = upload_artifact_url(thread_id, filename)
        if Path(filename).suffix.lower() == ".docx":
            extracted_images = []
            for image_filename in read_docx_sidecar_manifest(Path(f["path"]).parent, filename):
                image_path = Path(f["path"]).parent / image_filename
                if not image_path.is_file():
                    continue
                image_stat = image_path.stat()
                extracted_images.append(
                    {
                        "filename": image_filename,
                        "size": str(image_stat.st_size),
                        "path": str(image_path),
                        "extension": image_path.suffix,
                        "modified": image_stat.st_mtime,
                        "virtual_path": upload_virtual_path(image_filename),
                        "artifact_url": upload_artifact_url(thread_id, image_filename),
                    }
                )
            if extracted_images:
                f["extracted_images"] = extracted_images
        enriched_files.append(f)

    result["files"] = enriched_files
    result["count"] = len(enriched_files)
    return result
