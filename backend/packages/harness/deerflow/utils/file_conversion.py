"""File conversion utilities.

Converts document files (PDF, PPT, Excel, Word) to Markdown using markitdown.
No FastAPI or HTTP dependencies — pure utility functions.
"""

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# File extensions that should be converted to markdown
CONVERTIBLE_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
}

_DOCX_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
}


def _claim_unique_path(path: Path) -> Path:
    """Return a non-conflicting sibling path by appending ``_N`` if needed."""
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


async def convert_file_to_markdown(file_path: Path) -> Path | None:
    """Convert a file to markdown using markitdown.

    Args:
        file_path: Path to the file to convert.

    Returns:
        Path to the markdown file if conversion was successful, None otherwise.
    """
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(str(file_path))

        # Save as .md file with same name
        md_path = file_path.with_suffix(".md")
        md_path.write_text(result.text_content, encoding="utf-8")

        logger.info(f"Converted {file_path.name} to markdown: {md_path.name}")
        return md_path
    except Exception as e:
        logger.error(f"Failed to convert {file_path.name} to markdown: {e}")
        return None


async def extract_docx_images(file_path: Path) -> list[Path]:
    """Extract embedded images from a ``.docx`` file as sidecar upload files.

    The helper intentionally stays small in scope: it only unpacks
    ``word/media/*`` entries, applies basic image-type filtering, and writes
    deterministically named sidecar files next to the source document.

    Args:
        file_path: Path to the ``.docx`` file.

    Returns:
        List of extracted image paths in stable order. Returns an empty list for
        non-docx files or when no supported images are present.
    """
    if file_path.suffix.lower() != ".docx":
        return []

    try:
        with zipfile.ZipFile(file_path) as archive:
            media_entries = sorted(name for name in archive.namelist() if name.startswith("word/media/") and Path(name).suffix.lower() in _DOCX_IMAGE_EXTENSIONS)

            extracted_paths: list[Path] = []
            for index, member_name in enumerate(media_entries, start=1):
                suffix = Path(member_name).suffix.lower()
                target_path = _claim_unique_path(file_path.with_name(f"{file_path.stem}__image{index}{suffix}"))
                target_path.write_bytes(archive.read(member_name))
                extracted_paths.append(target_path)

        if extracted_paths:
            logger.info("Extracted %s embedded images from %s", len(extracted_paths), file_path.name)
        return extracted_paths
    except Exception as e:
        logger.error(f"Failed to extract images from {file_path.name}: {e}")
        return []
