"""Only the ink of an uploaded signature reaches the bill (owner, 18 Oct 2026).

The empty border round a signature made it print small in the signature box; a photo on
paper also printed its paper. trim_signature_data_uri cuts the border and makes the paper
see-through.
"""
import base64
import io
import unittest

from PIL import Image, ImageDraw

from core.bill_context import trim_signature_data_uri


def _uri(im: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    mime = "png" if fmt == "PNG" else "jpeg"
    return f"data:image/{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def _open(uri: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGBA")


class TheInkOnly(unittest.TestCase):
    def test_a_transparent_picture_loses_its_empty_border(self):
        im = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
        ImageDraw.Draw(im).line([(100, 80), (300, 120)], fill=(20, 40, 160, 255), width=6)
        out = _open(trim_signature_data_uri(_uri(im)))
        self.assertLess(out.width, 260)
        self.assertLess(out.height, 80)

    def test_a_photo_on_paper_loses_the_paper(self):
        im = Image.new("RGB", (400, 200), (214, 214, 220))
        ImageDraw.Draw(im).line([(100, 80), (300, 120)], fill=(30, 50, 150), width=6)
        out = _open(trim_signature_data_uri(_uri(im, "JPEG")))
        self.assertLess(out.width, 260)
        self.assertEqual(out.getpixel((0, 0))[3], 0)               # the paper is see-through
        self.assertEqual(out.getpixel((out.width // 2, out.height // 2))[3], 255)  # the ink is not

    def test_what_is_not_a_picture_comes_back_as_it_was(self):
        for uri in ("", "x", "data:image/png;base64,!!!"):
            self.assertEqual(trim_signature_data_uri(uri), uri)


if __name__ == "__main__":
    unittest.main()
