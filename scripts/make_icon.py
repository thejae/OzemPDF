"""Generate the application icon from the product's mark.

Run: python scripts/make_icon.py
Outputs app/build/icon.png (512px) and app/build/icon.ico.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (185, 59, 27, 255)
PAPER = (244, 243, 238, 255)
WHITE = (255, 255, 255, 255)


def render(size: int = 512) -> Image.Image:
    """Draw the vermilion tile with a white document mark."""
    scale = size / 512
    image = Image.new("RGBA", (size, size), PAPER)
    draw = ImageDraw.Draw(image)

    inset = round(56 * scale)
    radius = round(96 * scale)
    draw.rounded_rectangle(
        [inset, inset, size - inset, size - inset], radius=radius, fill=ACCENT
    )

    left, right = round(176 * scale), round(336 * scale)
    top, bottom = round(128 * scale), round(384 * scale)
    fold = round(64 * scale)
    draw.polygon(
        [
            (left, top),
            (right - fold, top),
            (right, top + fold),
            (right, bottom),
            (left, bottom),
        ],
        fill=WHITE,
    )
    # Folded corner: remove the triangle so the paper reads as a document.
    draw.polygon(
        [(right - fold, top), (right, top + fold), (right - fold, top + fold)],
        fill=ACCENT,
    )
    line = max(2, round(10 * scale))
    bar_left = round(left + 32 * scale)
    bar_width = round(right - left - 64 * scale)
    for offset, width in ((0.44, 0.56), (0.58, 0.42), (0.72, 0.28)):
        bar_top = round(bottom * offset + 12 * scale)
        draw.rounded_rectangle(
            [bar_left, bar_top, bar_left + round(bar_width * width), bar_top + line],
            radius=line // 2,
            fill=ACCENT,
        )
    return image


def main() -> None:
    """Write the PNG and ICO assets next to the Electron build config."""
    target = Path(__file__).resolve().parents[1] / "app" / "build"
    target.mkdir(parents=True, exist_ok=True)
    icon = render(512)
    icon.save(target / "icon.png")
    icon.save(
        target / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Wrote {target / 'icon.png'} and {target / 'icon.ico'}")


if __name__ == "__main__":
    main()
