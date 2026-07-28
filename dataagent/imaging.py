from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageEnhance, ImageOps, ImageStat

from .models import HardConstraints, ImageMetrics, TransformSpec


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def scan_images(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Image source directory does not exist: {root}")
    return sorted(
        (
            path.resolve()
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and path.resolve().is_relative_to(root)
        ),
        key=lambda path: str(path).lower(),
    )


def representative_sample(paths: list[Path], size: int) -> list[Path]:
    if size <= 0:
        raise ValueError("Sample size must be positive")
    if len(paths) <= size:
        return paths.copy()
    # Even spacing is deterministic and avoids selecting only one filename prefix.
    step = (len(paths) - 1) / (size - 1)
    indexes = sorted({round(index * step) for index in range(size)})
    return [paths[index] for index in indexes]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dhash(image: Image.Image, hash_size: int = 8) -> str:
    grayscale = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = list(
        grayscale.get_flattened_data()
        if hasattr(grayscale, "get_flattened_data")
        else grayscale.getdata()
    )
    bits = []
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for column in range(hash_size):
            bits.append(pixels[offset + column] > pixels[offset + column + 1])
    value = sum(1 << index for index, bit in enumerate(bits) if bit)
    return f"{value:0{hash_size * hash_size // 4}x}"


def _blur_score(image: Image.Image) -> float:
    gray = image.convert("L")
    if max(gray.size) > 512:
        gray.thumbnail((512, 512), Image.Resampling.LANCZOS)
    pixels = list(
        gray.get_flattened_data() if hasattr(gray, "get_flattened_data") else gray.getdata()
    )
    width, height = gray.size
    if width < 3 or height < 3:
        return 0.0
    gradients: list[float] = []
    for y in range(1, height - 1, 2):
        row = y * width
        for x in range(1, width - 1, 2):
            gx = abs(pixels[row + x + 1] - pixels[row + x - 1])
            gy = abs(pixels[row + width + x] - pixels[row - width + x])
            gradients.append(float(gx + gy))
    if not gradients:
        return 0.0
    mean = sum(gradients) / len(gradients)
    variance = sum((item - mean) ** 2 for item in gradients) / len(gradients)
    return round(math.sqrt(variance), 3)


def analyze_image(path: Path) -> ImageMetrics:
    sha = _sha256(path)
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            image_format = (image.format or path.suffix.lstrip(".")).lower()
            if image_format == "jpg":
                image_format = "jpeg"
            brightness = float(ImageStat.Stat(image.convert("L")).mean[0])
            return ImageMetrics(
                path=str(path.resolve()),
                sha256=sha,
                dhash=_dhash(image),
                width=width,
                height=height,
                aspect_ratio=round(width / height, 8) if height else 0.0,
                file_size_bytes=path.stat().st_size,
                format=image_format,
                brightness=round(brightness, 3),
                blur_score=_blur_score(image),
            )
    except Exception as exc:
        return ImageMetrics(
            path=str(path.resolve()),
            sha256=sha,
            dhash="",
            width=0,
            height=0,
            aspect_ratio=0.0,
            file_size_bytes=path.stat().st_size,
            format=path.suffix.lower().lstrip("."),
            brightness=0,
            blur_score=0,
            decode_ok=False,
            error=str(exc),
        )


def hard_constraint_failures(metrics: ImageMetrics, constraints: HardConstraints) -> list[str]:
    failures: list[str] = []
    if not metrics.decode_ok:
        return ["图片无法解码"]
    if constraints.min_width and metrics.width < constraints.min_width:
        failures.append(f"宽度小于 {constraints.min_width}")
    if constraints.min_height and metrics.height < constraints.min_height:
        failures.append(f"高度小于 {constraints.min_height}")
    if constraints.min_short_edge and min(metrics.width, metrics.height) < constraints.min_short_edge:
        failures.append(f"短边小于 {constraints.min_short_edge}")
    if constraints.max_width and metrics.width > constraints.max_width:
        failures.append(f"宽度大于 {constraints.max_width}")
    if constraints.max_height and metrics.height > constraints.max_height:
        failures.append(f"高度大于 {constraints.max_height}")
    ratio = metrics.width / metrics.height if metrics.height else 0
    if constraints.aspect_ratio_min and ratio < constraints.aspect_ratio_min:
        failures.append(f"宽高比小于 {constraints.aspect_ratio_min}")
    if constraints.aspect_ratio_max and ratio > constraints.aspect_ratio_max:
        failures.append(f"宽高比大于 {constraints.aspect_ratio_max}")
    if constraints.allowed_formats and metrics.format not in constraints.allowed_formats:
        failures.append(f"格式 {metrics.format} 不在允许列表")
    return failures


def transform_image(source: Path, destination: Path, spec: TransformSpec) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        if spec.center_crop_ratio:
            current = image.width / image.height
            target = spec.center_crop_ratio
            if current > target:
                width = round(image.height * target)
                left = (image.width - width) // 2
                image = image.crop((left, 0, left + width, image.height))
            elif current < target:
                height = round(image.width / target)
                top = (image.height - height) // 2
                image = image.crop((0, top, image.width, top + height))
        if spec.resize_long_edge and max(image.size) > spec.resize_long_edge:
            image.thumbnail((spec.resize_long_edge, spec.resize_long_edge), Image.Resampling.LANCZOS)
        if spec.autocontrast:
            image = ImageOps.autocontrast(image)
            image = ImageEnhance.Sharpness(image).enhance(1.05)
        output_format = (spec.output_format or source.suffix.lstrip(".") or "jpeg").lower()
        if output_format == "jpg":
            output_format = "jpeg"
        suffix = ".jpg" if output_format == "jpeg" else f".{output_format}"
        destination = destination.with_suffix(suffix)
        save_options = {"quality": 95, "optimize": True} if output_format == "jpeg" else {}
        image.save(destination, format=output_format.upper(), **save_options)
    return destination


def hashes(paths: Iterable[Path]) -> dict[str, str]:
    return {str(path.resolve()): _sha256(path) for path in paths}
