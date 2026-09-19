"""Regenerate desktop/src-tauri/icons from assets/Logo 01.png."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "Logo 01.png"
OUT = ROOT / "desktop" / "src-tauri" / "icons"


def _crop_mark(img: Image.Image) -> Image.Image:
    arr = img.load()
    w, h = img.size
    minx, miny, maxx, maxy = w, h, 0, 0
    step = max(1, min(w, h) // 400)
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b, a = arr[x, y]
            if a > 20 and (r > 25 or g > 25 or b > 25):
                minx = min(minx, x)
                miny = min(miny, y)
                maxx = max(maxx, x)
                maxy = max(maxy, y)
    if maxx <= minx:
        return img
    pad = int(0.06 * max(maxx - minx + 1, maxy - miny + 1))
    return img.crop(
        (
            max(0, minx - pad),
            max(0, miny - pad),
            min(w, maxx + pad + 1),
            min(h, maxy + pad + 1),
        )
    )


def fit(cropped: Image.Image, size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    c = cropped.copy()
    c.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas.paste(c, ((size - c.width) // 2, (size - c.height) // 2), c)
    return canvas


def main() -> None:
    img = Image.open(SRC).convert("RGBA")
    cropped = _crop_mark(img)
    sizes = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "icon.png": 512,
        "StoreLogo.png": 50,
        "Square30x30Logo.png": 30,
        "Square44x44Logo.png": 44,
        "Square71x71Logo.png": 71,
        "Square89x89Logo.png": 89,
        "Square107x107Logo.png": 107,
        "Square142x142Logo.png": 142,
        "Square150x150Logo.png": 150,
        "Square284x284Logo.png": 284,
        "Square310x310Logo.png": 310,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sz in sizes.items():
        fit(cropped, sz).save(OUT / name, "PNG")
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_imgs = [fit(cropped, s) for s in ico_sizes]
    ico_imgs[0].save(
        OUT / "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=ico_imgs[1:],
    )
    print(f"Wrote icons from {SRC} -> {OUT}")


if __name__ == "__main__":
    main()
