"""Middleware to inject uploaded files information into agent context."""

import logging
from pathlib import Path
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from deerflow.config.paths import Paths, get_paths
from deerflow.uploads.manager import enrich_file_listing, list_files_in_dir

logger = logging.getLogger(__name__)

_VIEW_IMAGE_GUIDANCE = (
    "  This .docx includes extracted images. Before answering questions that depend on screenshots, diagrams, flowcharts, or other visual details, use `view_image` on those image paths instead of relying on markdown alone."
)


class UploadsMiddlewareState(AgentState):
    """State schema for uploads middleware."""

    uploaded_files: NotRequired[list[dict] | None]


class UploadsMiddleware(AgentMiddleware[UploadsMiddlewareState]):
    """Middleware to inject uploaded files information into the agent context.

    Reads file metadata from the current message's additional_kwargs.files
    (set by the frontend after upload) and prepends an <uploaded_files> block
    to the last human message so the model knows which files are available.
    """

    state_schema = UploadsMiddlewareState

    def __init__(self, base_dir: str | None = None):
        """Initialize the middleware.

        Args:
            base_dir: Base directory for thread data. Defaults to Paths resolution.
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _create_files_message(self, new_files: list[dict], historical_files: list[dict]) -> str:
        """Create a formatted message listing uploaded files.

        Args:
            new_files: Files uploaded in the current message.
            historical_files: Files uploaded in previous messages.

        Returns:
            Formatted string inside <uploaded_files> tags.
        """
        lines = ["<uploaded_files>"]

        lines.append("The following files were uploaded in this message:")
        lines.append("")
        if new_files:
            for file in new_files:
                size_kb = int(file["size"]) / 1024
                size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
                lines.append(f"- {file['filename']} ({size_str})")
                lines.append(f"  Path: {file['path']}")
                if file.get("markdown_file") and file.get("markdown_path"):
                    lines.append(f"  Converted Markdown: {file['markdown_file']}")
                    lines.append(f"    Path: {file['markdown_path']}")
                for image in file.get("extracted_images", []):
                    lines.append("  Extracted images for vision analysis:")
                    lines.append(f"  Image: {image['filename']}")
                    lines.append(f"    Path: {image['path']}")
                if file.get("extracted_images"):
                    lines.append(_VIEW_IMAGE_GUIDANCE)
                lines.append("")
        else:
            lines.append("(empty)")

        if historical_files:
            lines.append("The following files were uploaded in previous messages and are still available:")
            lines.append("")
            for file in historical_files:
                size_kb = int(file["size"]) / 1024
                size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
                lines.append(f"- {file['filename']} ({size_str})")
                lines.append(f"  Path: {file['path']}")
                for image in file.get("extracted_images", []):
                    lines.append("  Extracted images for vision analysis:")
                    lines.append(f"  Image: {image['filename']}")
                    lines.append(f"    Path: {image['path']}")
                if file.get("extracted_images"):
                    lines.append(_VIEW_IMAGE_GUIDANCE)
                lines.append("")

        lines.append("You can read these files using the `read_file` tool with the paths shown above.")
        lines.append("</uploaded_files>")

        return "\n".join(lines)

    def _files_from_kwargs(self, message: HumanMessage, uploads_dir: Path | None = None) -> list[dict] | None:
        """Extract file info from message additional_kwargs.files.

        The frontend sends uploaded file metadata in additional_kwargs.files
        after a successful upload. Each entry has: filename, size (bytes),
        path (virtual path), status.

        Args:
            message: The human message to inspect.
            uploads_dir: Physical uploads directory used to verify file existence.
                         When provided, entries whose files no longer exist are skipped.

        Returns:
            List of file dicts with virtual paths, or None if the field is absent or empty.
        """
        kwargs_files = (message.additional_kwargs or {}).get("files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        files = []
        for f in kwargs_files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if not filename or Path(filename).name != filename:
                continue
            if uploads_dir is not None and not (uploads_dir / filename).is_file():
                continue
            file_info = {
                "filename": filename,
                "size": int(f.get("size") or 0),
                "path": f"/mnt/user-data/uploads/{filename}",
                "extension": Path(filename).suffix,
            }

            markdown_filename = f.get("markdown_file") or ""
            if markdown_filename and Path(markdown_filename).name == markdown_filename:
                if uploads_dir is None or (uploads_dir / markdown_filename).is_file():
                    file_info["markdown_file"] = markdown_filename
                    file_info["markdown_path"] = f"/mnt/user-data/uploads/{markdown_filename}"

            extracted_images = []
            for image in f.get("extracted_images") or []:
                if not isinstance(image, dict):
                    continue
                image_filename = image.get("filename") or ""
                if not image_filename or Path(image_filename).name != image_filename:
                    continue
                if uploads_dir is not None and not (uploads_dir / image_filename).is_file():
                    continue
                extracted_images.append(
                    {
                        "filename": image_filename,
                        "size": int(image.get("size") or 0),
                        "path": f"/mnt/user-data/uploads/{image_filename}",
                        "extension": Path(image_filename).suffix,
                        "virtual_path": f"/mnt/user-data/uploads/{image_filename}",
                        "artifact_url": image.get("artifact_url"),
                    }
                )
            if extracted_images:
                file_info["extracted_images"] = extracted_images

            files.append(file_info)
        return files if files else None

    def _collect_related_new_filenames(self, new_files: list[dict]) -> set[str]:
        """Collect filenames that should be excluded from historical listings."""
        related_filenames: set[str] = set()
        for file in new_files:
            filename = file["filename"]
            related_filenames.add(filename)
            markdown_filename = file.get("markdown_file")
            if markdown_filename:
                related_filenames.add(markdown_filename)
            for image in file.get("extracted_images", []):
                related_filenames.add(image["filename"])
        return related_filenames

    def _load_historical_files(self, uploads_dir: Path, thread_id: str, excluded_filenames: set[str]) -> list[dict]:
        """Load historical files using the shared grouping rules."""
        result = list_files_in_dir(uploads_dir)
        enrich_file_listing(result, thread_id)

        historical_files: list[dict] = []
        for file in result["files"]:
            if file["filename"] in excluded_filenames:
                continue
            historical_file = {
                "filename": file["filename"],
                "size": int(file["size"]),
                "path": file.get("virtual_path", f"/mnt/user-data/uploads/{file['filename']}"),
                "extension": file.get("extension", Path(file["filename"]).suffix),
            }
            extracted_images = []
            for image in file.get("extracted_images", []):
                if image["filename"] in excluded_filenames:
                    continue
                extracted_images.append(
                    {
                        "filename": image["filename"],
                        "size": int(image["size"]),
                        "path": image.get("virtual_path", f"/mnt/user-data/uploads/{image['filename']}"),
                        "extension": image.get("extension", Path(image["filename"]).suffix),
                        "virtual_path": image.get("virtual_path", f"/mnt/user-data/uploads/{image['filename']}"),
                        "artifact_url": image.get("artifact_url"),
                    }
                )
            if extracted_images:
                historical_file["extracted_images"] = extracted_images
            historical_files.append(historical_file)
        return historical_files

    @override
    def before_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """Inject uploaded files information before agent execution.

        New files come from the current message's additional_kwargs.files.
        Historical files are scanned from the thread's uploads directory,
        excluding the new ones.

        Prepends <uploaded_files> context to the last human message content.
        The original additional_kwargs (including files metadata) is preserved
        on the updated message so the frontend can read it from the stream.

        Args:
            state: Current agent state.
            runtime: Runtime context containing thread_id.

        Returns:
            State updates including uploaded files list.
        """
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]

        if not isinstance(last_message, HumanMessage):
            return None

        # Resolve uploads directory for existence checks
        thread_id = (runtime.context or {}).get("thread_id")
        uploads_dir = self._paths.sandbox_uploads_dir(thread_id) if thread_id else None

        # Get newly uploaded files from the current message's additional_kwargs.files
        new_files = self._files_from_kwargs(last_message, uploads_dir) or []

        # Collect historical files from the uploads directory (all except the new ones)
        historical_files: list[dict] = []
        if uploads_dir and uploads_dir.exists() and thread_id:
            historical_files = self._load_historical_files(uploads_dir, thread_id, self._collect_related_new_filenames(new_files))

        if not new_files and not historical_files:
            return None

        logger.debug(f"New files: {[f['filename'] for f in new_files]}, historical: {[f['filename'] for f in historical_files]}")

        # Create files message and prepend to the last human message content
        files_message = self._create_files_message(new_files, historical_files)

        # Extract original content - handle both string and list formats
        original_content = ""
        if isinstance(last_message.content, str):
            original_content = last_message.content
        elif isinstance(last_message.content, list):
            text_parts = []
            for block in last_message.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            original_content = "\n".join(text_parts)

        # Create new message with combined content.
        # Preserve additional_kwargs (including files metadata) so the frontend
        # can read structured file info from the streamed message.
        updated_message = HumanMessage(
            content=f"{files_message}\n\n{original_content}",
            id=last_message.id,
            additional_kwargs=last_message.additional_kwargs,
        )

        messages[last_message_index] = updated_message

        return {
            "uploaded_files": new_files,
            "messages": messages,
        }
