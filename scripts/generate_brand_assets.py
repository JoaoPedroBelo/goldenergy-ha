"""Rasterise the Goldenergy brand assets from the official SVG.

Source of truth is `custom_components/goldenergy/brand/goldenergy-logo.svg`, taken
verbatim from the customer area
(https://clientes.goldenergy.pt/sws-content/uploads/2023/06/logo-roxo.svg). The
logo is used nominatively, to identify the service this integration talks to — the
same basis on which the `home-assistant/brands` repository carries third-party
logos.

Outputs follow the Home Assistant brand spec:

    icon.png      256x256        square, the ring mark (the "o" of "gold") alone
    icon@2x.png   512x512
    logo.png      <=512 x <=256  the full stacked lockup
    logo@2x.png   <=1024 x <=512

The ring is the same mark Goldenergy uses as its favicon, so the icon stays
recognisable at small sizes where the wordmark would not.

Run with `make brand` (needs Pillow + cairosvg; cairosvg needs the cairo library,
`brew install cairo` on macOS).

This script lives outside `custom_components/` on purpose: the HACS release zip
only packs the component directory, so users never download it.
"""

from __future__ import annotations

import io
import pathlib

import cairosvg
from PIL import Image

BRAND = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components/goldenergy/brand"
)
SVG = BRAND / "goldenergy-logo.svg"

# The SVG declares viewBox "0 0 295.349 179.789".
VIEWBOX_WIDTH = 295.349

# A box, in viewBox units, holding the ring mark and nothing else: it sits between
# the "g" (ends ~79) and the "l" (starts ~183), above the "energy" line (starts
# ~125). Cropping a box rather than extracting paths keeps this robust against the
# SVG being re-exported; ``trim`` then tightens it to the mark itself.
MARK_BOX = (84.0, 0.0, 178.0, 120.0)

# Breathing room around the mark inside the square icon canvas.
ICON_MARGIN = 0.08

# Render well above the target size, then downsample with Lanczos.
RENDER_WIDTH = 4096


def render_svg(width: int) -> Image.Image:
    """Render the source SVG to an RGBA image of the given width."""
    png = cairosvg.svg2png(url=str(SVG), output_width=width)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def fit_within(img: Image.Image, max_width: int, max_height: int) -> Image.Image:
    """Downscale ``img`` to fit the box, preserving aspect ratio."""
    scale = min(max_width / img.width, max_height / img.height)
    return img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.LANCZOS,
    )


def trim(img: Image.Image) -> Image.Image:
    """Drop fully transparent borders."""
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def make_logo(max_width: int, max_height: int) -> Image.Image:
    """The full lockup, trimmed of transparent padding."""
    return fit_within(trim(render_svg(RENDER_WIDTH)), max_width, max_height)


def make_icon(size: int) -> Image.Image:
    """The ring mark alone, centred on a transparent square canvas."""
    full = render_svg(RENDER_WIDTH)
    scale = full.width / VIEWBOX_WIDTH
    box = tuple(round(v * scale) for v in MARK_BOX)
    mark = trim(full.crop(box))

    inner = round(size * (1 - 2 * ICON_MARGIN))
    mark = fit_within(mark, inner, inner)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(mark, ((size - mark.width) // 2, (size - mark.height) // 2), mark)
    return canvas


def main() -> None:
    if not SVG.is_file():
        raise SystemExit(f"missing source SVG: {SVG}")

    assets = (
        ("icon.png", make_icon(256)),
        ("icon@2x.png", make_icon(512)),
        ("logo.png", make_logo(512, 256)),
        ("logo@2x.png", make_logo(1024, 512)),
    )
    for name, img in assets:
        path = BRAND / name
        img.save(path, "PNG", optimize=True)
        size = path.stat().st_size / 1024
        print(f"  {name:14} {img.width}x{img.height:<5} {size:6.1f} KB")


if __name__ == "__main__":
    main()
