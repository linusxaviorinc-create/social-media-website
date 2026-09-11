#!/usr/bin/env python3
"""
Compose a 1080x1920 Instagram Story image from an event flyer.

Layout, measured from the approved Kal-El sample:

- The whole canvas is filled with a muted version of the flyer's dominant
  color, the way Instagram auto color-matches a Story background. It is
  darkened as needed so the white Annex logos read clearly on it.
- The flyer is centered horizontally, aspect preserved and never cropped. Its
  top sits just under the top logo row, and it is scaled uniformly so its
  bottom edge runs through the ticket pill's horizontal center line (up to the
  sample's 970px width).
- The transparent Annex plate is split at its clear middle into a top row and a
  bottom row of logos. Each row spans the full canvas width, which makes it
  about 347px tall, as in the sample. The top row sits just below the top edge
  and the bottom row just above the bottom edge, so the "THE ANNEX" wordmark
  and the stairs never clip. The top row may run slightly over the flyer's top
  edge.
- One white pill with black "TICKETS IN BIO!!" text, centered horizontally, sits
  half on the flyer and half below it. Its bottom edge touches the top of the
  "the" in the bottom row's "THE ANNEX" wordmark. Meta does not allow stickers, including link
  stickers, on Stories published through the API, and documents no caption for
  API Stories, so the ticket link lives in the profile bio.
"""

from __future__ import annotations

import colorsys
from pathlib import Path
from typing import Any

from platforms import ROOT

try:  # Pillow is only needed when an image is actually composed.
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:  # pragma: no cover - surfaced with a clear message below.
    Image = None


STORY_WIDTH = 1080
STORY_HEIGHT = 1920

DEFAULT_PLATE = ROOT / "social" / "assets" / "annex_story_plate.png"
DEFAULT_TICKET_TEXT = "TICKETS IN BIO!!"

# The approved sample places the flyer 970px wide on the 1080px canvas.
FLYER_WIDTH_RATIO = 970 / 1080

# Background color: the flyer's dominant color, then muted. These factors take
# the Kal-El tour flyer's dominant RGB(194,116,103) to about RGB(176,111,100),
# matching the approved sample's clay field.
FIELD_SATURATION_SCALE = 0.76
FIELD_SATURATION_MAX = 0.40
FIELD_LIGHTNESS_SCALE = 0.93
FIELD_LIGHTNESS_RANGE = (0.30, 0.60)  # never near black or near white
MIN_CONTRAST_WITH_WHITE = 3.0  # so the white logos stay legible

# Instagram draws its reply bar over roughly the bottom 250px of a Story; a
# warning is raised if the ticket label ever reaches into it.
SAFE_BOTTOM = STORY_HEIGHT - 250
LABEL_PAD_X = 34
LABEL_PAD_Y = 20
LABEL_FILL = (255, 255, 255, 255)  # white pill
LABEL_TEXT_COLOR = (0, 0, 0, 255)  # black text

# Alpha at or below this is treated as empty when finding the logo bands.
BAND_ALPHA_THRESHOLD = 8
# Clearance between each logo row and the canvas edge, so nothing clips.
BAND_EDGE_MARGIN = 12
# How far a logo row may run over the flyer's edge before a very tall flyer is
# narrowed to make room, so the rows never cover more than its border.
MAX_BAND_OVERLAP = 10

FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


def _require_pillow() -> None:
    if Image is None:
        raise SystemExit(
            "Pillow is required to compose Story images.\n"
            "Install it with: python3 -m pip install -r requirements.txt"
        )


def load_plate(plate_path: Path) -> "Image.Image":
    """Load the plate and refuse it unless it carries real transparency.

    A flat RGB copy would paint an opaque block over the whole Story, so this
    stops instead of producing an image that looks broken.
    """
    _require_pillow()
    if not plate_path.exists():
        raise SystemExit(f"Annex plate not found: {plate_path}")

    plate = Image.open(plate_path)
    plate.load()
    if plate.mode != "RGBA":
        raise SystemExit(
            f"The Annex plate is {plate.mode}, not RGBA, so it has no transparency: {plate_path}\n"
            "Re-download the original PNG. Do not use a flattened copy."
        )
    low, high = plate.getchannel("A").getextrema()
    if low == high:
        raise SystemExit(
            f"The Annex plate's alpha channel is a flat {low}, so it has no real transparency: "
            f"{plate_path}"
        )
    return plate


def split_plate(plate: "Image.Image") -> tuple["Image.Image", "Image.Image"]:
    """Cut the plate at its clear middle into a top band and a bottom band.

    Each band is trimmed to its own artwork, so its top and bottom edges are the
    wordmark and the stairs rather than empty transparent rows.
    """
    width, height = plate.size
    mask = plate.getchannel("A").point(lambda value: 255 if value > BAND_ALPHA_THRESHOLD else 0)
    top = mask.crop((0, 0, width, height // 2)).getbbox()
    bottom = mask.crop((0, height // 2, width, height)).getbbox()
    if top is None or bottom is None:
        raise SystemExit("Could not find logo bands at the top and bottom of the Annex plate.")
    top_band = plate.crop(top)
    bottom_band = plate.crop((bottom[0], height // 2 + bottom[1], bottom[2], height // 2 + bottom[3]))
    return top_band, bottom_band


def full_width_band(band: "Image.Image") -> dict[str, Any]:
    """Scale a logo row to span the full canvas width, aspect preserved."""
    scale = STORY_WIDTH / band.width
    size = (STORY_WIDTH, max(1, round(band.height * scale)))
    return {
        "image": band.resize(size, Image.LANCZOS),
        "source_size": list(band.size),
        "size": list(size),
        "scale": round(scale, 4),
    }


def dominant_color(image: "Image.Image", colors: int = 8) -> tuple[tuple[int, int, int], float]:
    """Most common color cluster in the image, and the share of pixels it covers."""
    small = image.convert("RGB").resize((160, 200), Image.BILINEAR)
    quantized = small.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    count, index = max(quantized.getcolors())
    rgb = tuple(palette[index * 3: index * 3 + 3])
    return rgb, count / (small.width * small.height)


def contrast_with_white(rgb: tuple[int, int, int]) -> float:
    """WCAG contrast ratio between white and the given color."""
    linear = [
        (v / 255) / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4
        for v in rgb
    ]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    return 1.05 / (luminance + 0.05)


def field_color(image: "Image.Image") -> tuple[tuple[int, int, int], dict[str, Any]]:
    """Muted background derived from the flyer's dominant color."""
    source, share = dominant_color(image)
    hue, lightness, saturation = colorsys.rgb_to_hls(*(v / 255 for v in source))

    saturation = min(saturation * FIELD_SATURATION_SCALE, FIELD_SATURATION_MAX)
    low, high = FIELD_LIGHTNESS_RANGE
    lightness = min(max(lightness * FIELD_LIGHTNESS_SCALE, low), high)

    def to_rgb(l: float) -> tuple[int, int, int]:
        return tuple(round(v * 255) for v in colorsys.hls_to_rgb(hue, l, saturation))

    rgb = to_rgb(lightness)
    # Darken further only if white logos would not read clearly.
    while contrast_with_white(rgb) < MIN_CONTRAST_WITH_WHITE and lightness > low:
        lightness = max(low, lightness - 0.01)
        rgb = to_rgb(lightness)

    return rgb, {
        "dominant_rgb": list(source),
        "dominant_share": round(share, 3),
        "contrast_with_white": round(contrast_with_white(rgb), 2),
    }


def load_font(size: int, font_path: str | None = None) -> "ImageFont.ImageFont":
    for candidate in ([font_path] if font_path else []) + list(FONT_CANDIDATES):
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def artwork_top(band_image: "Image.Image", x0: int, x1: int) -> int:
    """Rows from a band's top edge down to its first visible artwork within x0-x1."""
    alpha = band_image.getchannel("A").crop((max(0, x0), 0, min(band_image.width, x1), band_image.height))
    box = alpha.point(lambda value: 255 if value > BAND_ALPHA_THRESHOLD else 0).getbbox()
    return box[1] if box else 0


def label_size(text: str, font: "ImageFont.ImageFont") -> tuple[int, int]:
    """Width and height of the label's backing pill."""
    left, top, right, bottom = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
        (0, 0), text, font=font
    )
    return (right - left) + 2 * LABEL_PAD_X, (bottom - top) + 2 * LABEL_PAD_Y


def draw_ticket_label(
    canvas: "Image.Image",
    text: str,
    center_y: int,
    font: "ImageFont.ImageFont",
) -> list[int]:
    """Draw black text on a white pill. Returns the box with an exclusive end."""
    box_width, box_height = label_size(text, font)
    x0 = (STORY_WIDTH - box_width) // 2
    y0 = center_y - box_height // 2
    box = [x0, y0, x0 + box_width, y0 + box_height]

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # Pillow includes the end coordinate, so subtract 1 to draw exactly box_height rows.
    draw.rounded_rectangle(
        [box[0], box[1], box[2] - 1, box[3] - 1], radius=box_height // 2, fill=LABEL_FILL
    )
    draw.text((STORY_WIDTH // 2, center_y), text, font=font, fill=LABEL_TEXT_COLOR, anchor="mm")
    canvas.alpha_composite(overlay)
    return box


def compose_story(
    flyer_path: Path,
    output_path: Path,
    plate_path: Path = DEFAULT_PLATE,
    ticket_text: str = DEFAULT_TICKET_TEXT,
    flyer_width_ratio: float = FLYER_WIDTH_RATIO,
    font_size: int = 44,
    font_path: str | None = None,
    jpeg_quality: int = 92,
) -> dict[str, Any]:
    """Build the Story image and describe exactly how it was laid out."""
    _require_pillow()
    if not flyer_path.exists():
        raise SystemExit(f"Flyer not found: {flyer_path}")

    warnings: list[str] = []

    flyer = Image.open(flyer_path)
    flyer.load()
    flyer = (ImageOps.exif_transpose(flyer) or flyer).convert("RGB")
    original_size = flyer.size

    background, color_info = field_color(flyer)

    # Logo rows span the full width and hug the top and bottom edges.
    source_plate = load_plate(plate_path)
    top_source, bottom_source = split_plate(source_plate)
    top_band = full_width_band(top_source)
    bottom_band = full_width_band(bottom_source)
    top_band["position"] = [0, BAND_EDGE_MARGIN]
    bottom_band["position"] = [0, STORY_HEIGHT - BAND_EDGE_MARGIN - bottom_band["size"][1]]
    top_row_bottom = BAND_EDGE_MARGIN + top_band["size"][1]
    bottom_row_top = bottom_band["position"][1]

    # The pill's bottom edge touches the top of the bottom row's artwork right
    # beneath it (the "the" of "THE ANNEX"), and the flyer's bottom edge runs
    # through the pill's horizontal center line.
    font = load_font(font_size, font_path) if ticket_text else None
    pill_top = pill_bottom = None
    if ticket_text:
        box_width, box_height = label_size(ticket_text, font)
        pill_x0 = (STORY_WIDTH - box_width) // 2
        pill_bottom = bottom_row_top + artwork_top(bottom_band["image"], pill_x0, pill_x0 + box_width)
        pill_top = pill_bottom - box_height
        flyer_bottom = pill_top + box_height // 2
    else:
        flyer_bottom = bottom_row_top

    # Scale the flyer uniformly (aspect preserved, never cropped or stretched) to
    # fill from just under the top row down to that line, up to the sample's width.
    flyer_top_limit = top_row_bottom - MAX_BAND_OVERLAP
    max_height = flyer_bottom - flyer_top_limit
    width = round(STORY_WIDTH * flyer_width_ratio)
    height = round(width * flyer.height / flyer.width)
    if height > max_height:
        height = max_height
        width = round(height * flyer.width / flyer.height)
    resized = flyer.resize((width, height), Image.LANCZOS)
    x = (STORY_WIDTH - width) // 2
    y = flyer_bottom - height

    canvas = Image.new("RGBA", (STORY_WIDTH, STORY_HEIGHT), background + (255,))
    canvas.paste(resized, (x, y))
    for band in (top_band, bottom_band):
        canvas.alpha_composite(band["image"], tuple(band["position"]))

    label_box = None
    if ticket_text:
        label_box = draw_ticket_label(canvas, ticket_text, pill_top + box_height // 2, font)
        on_flyer, below_flyer = flyer_bottom - label_box[1], label_box[3] - flyer_bottom
        if label_box[3] != pill_bottom or abs(on_flyer - below_flyer) > 1:
            raise SystemExit("Layout error: the ticket pill is not centered on the flyer's bottom edge.")
        if label_box[3] > SAFE_BOTTOM:
            warnings.append(
                "Ticket label reaches into the bottom 250px, where Instagram's reply bar can cover it."
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(
        output_path,
        format="JPEG",
        quality=jpeg_quality,
        optimize=True,
        progressive=True,
        subsampling="4:2:0",
    )

    # Read the file back so the reported size is what was actually written.
    with Image.open(output_path) as written:
        output_size = list(written.size)
        output_format = written.format
    if output_size != [STORY_WIDTH, STORY_HEIGHT]:
        raise SystemExit(f"Composed Story came out {output_size}, not {STORY_WIDTH}x{STORY_HEIGHT}.")

    return {
        "flyer": str(flyer_path),
        "output": str(output_path),
        "output_size": output_size,
        "output_format": output_format,
        "background_rgb": list(background),
        **color_info,
        "flyer_original_size": list(original_size),
        "flyer_placed_size": [width, height],
        "flyer_position": [x, y],
        "flyer_width_pct": round(width / STORY_WIDTH * 100, 1),
        "gap_top_px": y,
        "gap_bottom_px": STORY_HEIGHT - (y + height),
        "gap_side_px": x,
        "flyer_cropped": False,
        "plate": str(plate_path),
        "plate_source_size": list(source_plate.size),
        # Positive overlap means the row runs over the flyer; negative is a gap.
        "top_band": {
            **{k: v for k, v in top_band.items() if k != "image"},
            "margin_to_canvas_edge_px": BAND_EDGE_MARGIN,
            "overlap_with_flyer_px": top_row_bottom - y,
        },
        "bottom_band": {
            **{k: v for k, v in bottom_band.items() if k != "image"},
            "margin_to_canvas_edge_px": BAND_EDGE_MARGIN,
            "overlap_with_flyer_px": flyer_bottom - bottom_row_top,
        },
        "ticket_text": ticket_text,
        "ticket_label_on_flyer_px": (flyer_bottom - label_box[1]) if label_box else None,
        "ticket_label_below_flyer_px": (label_box[3] - flyer_bottom) if label_box else None,
        "ticket_label_box": label_box,
        "ticket_label_touches_bottom_row_at_y": pill_bottom,
        "size_bytes": output_path.stat().st_size,
        "warnings": warnings,
    }
