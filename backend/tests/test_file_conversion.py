"""Tests for docx image extraction helpers."""

import asyncio
import zipfile
from pathlib import Path

from deerflow.utils.file_conversion import extract_docx_images


def _write_docx(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_extract_docx_images_returns_stably_named_supported_images(tmp_path):
    docx_path = tmp_path / "report.docx"
    _write_docx(
        docx_path,
        {
            "word/media/image2.jpeg": b"jpeg",
            "word/media/image1.png": b"png",
            "word/media/ignored.bin": b"bin",
        },
    )

    extracted = asyncio.run(extract_docx_images(docx_path))

    assert [path.name for path in extracted] == [
        "report__image1.png",
        "report__image2.jpeg",
    ]
    assert extracted[0].read_bytes() == b"png"
    assert extracted[1].read_bytes() == b"jpeg"


def test_extract_docx_images_returns_empty_for_non_docx_files(tmp_path):
    text_path = tmp_path / "notes.txt"
    text_path.write_text("hello", encoding="utf-8")

    extracted = asyncio.run(extract_docx_images(text_path))

    assert extracted == []


def test_extract_docx_images_returns_empty_for_invalid_docx(tmp_path):
    docx_path = tmp_path / "broken.docx"
    docx_path.write_bytes(b"not-a-zip")

    extracted = asyncio.run(extract_docx_images(docx_path))

    assert extracted == []
