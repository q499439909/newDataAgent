from __future__ import annotations

from pathlib import Path


SUPPORTED_MEDIA_EXTENSIONS: dict[str, frozenset[str]] = {
    "image": frozenset(
        {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
            ".tif",
            ".tiff",
        }
    ),
    "text": frozenset(
        {
            ".txt",
            ".md",
            ".markdown",
            ".rst",
            ".html",
            ".htm",
            ".xml",
        }
    ),
    "audio": frozenset(
        {
            ".wav",
            ".mp3",
            ".flac",
            ".ogg",
            ".m4a",
            ".aac",
            ".opus",
        }
    ),
    "video": frozenset(
        {
            ".mp4",
            ".mov",
            ".mkv",
            ".avi",
            ".webm",
            ".m4v",
            ".mpeg",
            ".mpg",
        }
    ),
}


def scan_dataset_assets(
    root: Path,
    *,
    modalities: frozenset[str],
) -> list[Path]:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Dataset source directory does not exist: {root}")
    selected_modalities = modalities or frozenset({"image"})
    unknown = selected_modalities.difference(SUPPORTED_MEDIA_EXTENSIONS)
    if unknown:
        raise ValueError(
            "Unsupported dataset modalities: " + ", ".join(sorted(unknown))
        )
    extensions = frozenset(
        extension
        for modality in selected_modalities
        for extension in SUPPORTED_MEDIA_EXTENSIONS[modality]
    )
    return sorted(
        (
            path.resolve()
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in extensions
            and path.resolve().is_relative_to(root)
        ),
        key=lambda path: str(path).lower(),
    )


__all__ = ["SUPPORTED_MEDIA_EXTENSIONS", "scan_dataset_assets"]
