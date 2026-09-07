#!/usr/bin/env python3
"""
Turn an approved flyer into a JPEG that Instagram will accept unchanged.

Meta's publishing API accepts JPEG only, and Instagram crops anything outside
the 4:5 to 1.91:1 aspect range. Event flyers are usually 8.5x11 (about 0.77),
which is just outside that range, so the default here pads the image instead of
cropping it. Padding keeps every word on the flyer readable; cropping would cut
the top or bottom off whatever Instagram decided to trim.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:  # Pillow is only needed when an image is actually prepared.
    from PIL import Image, ImageColor, ImageOps
except ImportError:  # pragma: no cover - surfaced with a clear message below.
    Image = None
    ImageColor = None
    ImageOps = None


# Instagram feed image limits.
MIN_ASPECT = 0.8  # 4:5 portrait
MAX_ASPECT = 1.91  # 1.91:1 landscape
MAX_WIDTH = 1440
RECOMMENDED_MIN_WIDTH = 600
MAX_BYTES = 8 * 1024 * 1024

FIT_CHOICES = ("pad", "crop", "error")


def _require_pillow() -> None:
    if Image is None:
        raise SystemExit(
            "Pillow is required to prepare images.\n"
            "Install it with: python3 -m pip install -r requirements.txt"
        )


def _open_image(source: Path, workdir: Path) -> "Image.Image":
    """Open an image, falling back to macOS `sips` for formats Pillow cannot read.

    Flyers pulled from an iPhone or Google Drive are often HEIC, which Pillow
    does not decode out of the box.
    """
    try:
        image = Image.open(source)
        image.load()
        return image
    except Exception as pillow_error:
        sips = shutil.which("sips")
        if not sips:
            raise SystemExit(f"Could not read image {source}: {pillow_error}")

        converted = workdir / f"{source.stem}-sips.jpg"
        result = subprocess.run(
            [sips, "-s", "format", "jpeg", str(source), "--out", str(converted)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not converted.exists():
            raise SystemExit(
                f"Could not read image {source}: {pillow_error}\n"
                f"sips fallback also failed: {result.stderr.strip()}"
            )
        image = Image.open(converted)
        image.load()
        return image


def _flatten(image: "Image.Image", background: tuple[int, int, int]) -> "Image.Image":
    """Drop transparency onto a solid background. JPEG has no alpha channel."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, background)
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return image.convert("RGB")


def _pad_to_aspect(image: "Image.Image", background: tuple[int, int, int]) -> "Image.Image":
    width, height = image.size
    ratio = width / height

    if ratio < MIN_ASPECT:
        target_width = max(width, math.ceil(height * MIN_ASPECT))
        target_height = height
    elif ratio > MAX_ASPECT:
        target_width = width
        target_height = max(height, math.ceil(width / MAX_ASPECT))
    else:
        return image

    canvas = Image.new("RGB", (target_width, target_height), background)
    canvas.paste(image, ((target_width - width) // 2, (target_height - height) // 2))
    return canvas


def _crop_to_aspect(image: "Image.Image") -> "Image.Image":
    width, height = image.size
    ratio = width / height

    if ratio < MIN_ASPECT:
        target_height = math.floor(width / MIN_ASPECT)
        offset = (height - target_height) // 2
        return image.crop((0, offset, width, offset + target_height))
    if ratio > MAX_ASPECT:
        target_width = math.floor(height * MAX_ASPECT)
        offset = (width - target_width) // 2
        return image.crop((offset, 0, offset + target_width, height))
    return image


def _save_under_limit(image: "Image.Image", target: Path, quality: int) -> dict[str, Any]:
    """Write a progressive JPEG, stepping quality down until it fits the 8MB cap."""
    attempts: list[dict[str, Any]] = []
    working = image
    current_quality = max(40, min(95, quality))

    for _ in range(8):
        working.save(
            target,
            format="JPEG",
            quality=current_quality,
            optimize=True,
            progressive=True,
            subsampling="4:2:0",
        )
        size = target.stat().st_size
        attempts.append({"quality": current_quality, "width": working.size[0], "bytes": size})
        if size <= MAX_BYTES:
            return {"quality": current_quality, "size_bytes": size, "attempts": attempts}

        if current_quality > 60:
            current_quality -= 10
            continue

        new_width = int(working.size[0] * 0.85)
        if new_width < RECOMMENDED_MIN_WIDTH:
            break
        new_height = int(working.size[1] * (new_width / working.size[0]))
        working = working.resize((new_width, new_height), Image.LANCZOS)

    raise SystemExit(f"Could not compress {target.name} under {MAX_BYTES // (1024 * 1024)}MB.")


def prepare_jpeg(
    source: Path,
    output_dir: Path,
    fit: str = "pad",
    pad_color: str = "#ffffff",
    quality: int = 90,
    max_width: int = MAX_WIDTH,
    stem: str | None = None,
) -> dict[str, Any]:
    """Convert one image into an Instagram-ready JPEG and describe what changed."""
    _require_pillow()

    if fit not in FIT_CHOICES:
        raise SystemExit(f"Unknown fit mode '{fit}'. Choose one of: {', '.join(FIT_CHOICES)}")
    if not source.exists():
        raise SystemExit(f"Asset not found: {source}")

    output_dir.mkdir(parents=True, exist_ok=True)
    background = ImageColor.getrgb(pad_color)
    warnings: list[str] = []
    actions: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        image = _open_image(source, workdir)
        original_format = (image.format or "unknown").upper()
        oriented = ImageOps.exif_transpose(image) or image
        if oriented.size != image.size:
            actions.append("applied EXIF orientation")

        original_size = oriented.size
        original_ratio = original_size[0] / original_size[1]

        working = _flatten(oriented, background)
        if original_format != "JPEG":
            actions.append(f"converted {original_format} to JPEG")

        if original_ratio < MIN_ASPECT or original_ratio > MAX_ASPECT:
            if fit == "error":
                raise SystemExit(
                    f"{source.name} has an aspect ratio of {original_ratio:.3f}, outside Instagram's "
                    f"{MIN_ASPECT}-{MAX_ASPECT} range. Re-run with --fit pad or --fit crop."
                )
            if fit == "pad":
                working = _pad_to_aspect(working, background)
                actions.append(f"padded to {working.size[0]}x{working.size[1]} with {pad_color}")
            else:
                working = _crop_to_aspect(working)
                actions.append(f"center-cropped to {working.size[0]}x{working.size[1]}")
                warnings.append("Cropping can cut text off a flyer. Check the output before publishing.")

        if working.size[0] > max_width:
            new_height = int(working.size[1] * (max_width / working.size[0]))
            working = working.resize((max_width, new_height), Image.LANCZOS)
            actions.append(f"resized down to {max_width}x{new_height}")

        if working.size[0] < RECOMMENDED_MIN_WIDTH:
            warnings.append(
                f"Final width is {working.size[0]}px, below Instagram's recommended "
                f"{RECOMMENDED_MIN_WIDTH}px. The post may look soft."
            )

        final_ratio = working.size[0] / working.size[1]
        if final_ratio < MIN_ASPECT or final_ratio > MAX_ASPECT:
            raise SystemExit(
                f"Internal check failed: prepared ratio {final_ratio:.4f} is still outside "
                f"{MIN_ASPECT}-{MAX_ASPECT}. Instagram would crop this image."
            )

        target = output_dir / f"{stem or source.stem}.jpg"
        saved = _save_under_limit(working, target, quality)

    return {
        "source": str(source),
        "output": str(target),
        "original_format": original_format,
        "original_size": list(original_size),
        "original_aspect_ratio": round(original_ratio, 4),
        "final_size": [working.size[0], working.size[1]],
        "final_aspect_ratio": round(final_ratio, 4),
        "fit": fit,
        "jpeg_quality": saved["quality"],
        "size_bytes": saved["size_bytes"],
        "actions": actions,
        "warnings": warnings,
    }
