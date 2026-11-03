"""Unit tests for URL validation, error mapping, and logo handling."""
import base64
from io import BytesIO

import pytest
from PIL import Image

from app.errors import AppError
from app.images import MAX_LOGO_BYTES, embed_artwork, prepare_logo
from app.validation import decode_payload, normalize_url


def test_ascii_url_is_preserved():
    value = "https://example.com/a?x=1&y=%E4%B8%AD#hello"
    assert normalize_url(value) == value


def test_unicode_url_becomes_ascii():
    assert normalize_url("https://例子.测试/中文") == "https://xn--fsqu00a.xn--0zwm56d/%E4%B8%AD%E6%96%87"


def test_idna_2008_is_used():
    assert normalize_url("https://faß.de/") == "https://xn--fa-hia.de/"


@pytest.mark.parametrize("value", ["https://a.b/?", "https://a.b/#", "https://a.b/#a#b"])
def test_empty_delimiters_are_preserved(value):
    assert normalize_url(value) == value


@pytest.mark.parametrize("value", ["", "javascript:alert(1)", "example.com", "https:///x",
                                  "https://u:p@example.com/", "https://a.b/a b", "https://a.b/\n"])
def test_invalid_urls_are_rejected(value):
    with pytest.raises(AppError) as error:
        normalize_url(value)
    assert error.value.code == "INVALID_URL" and error.value.status == 400


def test_overlong_url_is_rejected():
    with pytest.raises(AppError):
        normalize_url("https://example.com/" + "a" * 600)


def test_decode_payload_requires_wx_code_and_exact_bytes():
    assert decode_payload("DECODED_TYPE=WX_CODE\nPAYLOAD_BASE64=aHR0cHM6Ly9saW51eC5kby8=\n") == b"https://linux.do/"
    for value in ("DECODED_TYPE=QR_CODE\nPAYLOAD_BASE64=YQ==\n", "DECODED_TYPE=WX_CODE\n",
                  "DECODED_TYPE=WX_CODE\nPAYLOAD_BASE64=YQ==\nPAYLOAD_BASE64=Yg==\n"):
        with pytest.raises(AppError):
            decode_payload(value)


def test_unicode_separator_cannot_override_payload():
    payload = "x\u0085PAYLOAD_BASE64=YQ==".encode()
    log = "DECODED_TYPE=WX_CODE\nPAYLOAD_BASE64=" + base64.b64encode(payload).decode() + "\n"
    assert decode_payload(log) == payload


def test_logo_is_normalized_to_square_png():
    source = BytesIO()
    Image.new("RGB", (1200, 400), "blue").save(source, format="PNG")
    prepared = prepare_logo(source.getvalue())
    with Image.open(BytesIO(prepared)) as image:
        assert image.format == "PNG" and image.size == (512, 512)


def _prepared_pixels(data: bytes) -> Image.Image:
    return Image.open(BytesIO(prepare_logo(data))).convert("RGB")


def test_exif_rotated_jpeg_is_scaled_and_oriented():
    # Left half red, right half blue; orientation 6 rotates it so red ends up on top.
    source = Image.new("RGB", (2000, 1000), "blue")
    source.paste("red", (0, 0, 1000, 1000))
    exif = Image.Exif()
    exif[0x0112] = 6
    data = BytesIO()
    source.save(data, format="JPEG", quality=95, exif=exif)
    image = _prepared_pixels(data.getvalue())
    top, bottom = image.getpixel((256, 150)), image.getpixel((256, 362))
    assert top[0] > 200 > top[2] and bottom[2] > 200 > bottom[0]


def test_transparent_margin_is_cropped_and_opaque_logo_fills_the_box():
    source = Image.new("RGBA", (1000, 1000), (0, 0, 0, 0))
    source.paste((255, 0, 0, 255), (450, 450, 550, 550))
    data = BytesIO()
    source.save(data, format="PNG")
    image = _prepared_pixels(data.getvalue())
    # The 100px artwork stays 100px (thumbnail never upscales), centred on white.
    assert image.getpixel((256, 256)) == (255, 0, 0)
    assert image.getpixel((256 - 49, 256)) == (255, 0, 0) and image.getpixel((256 - 52, 256)) == (255, 255, 255)
    opaque = BytesIO()
    Image.new("RGBA", (1000, 1000), (0, 255, 0, 255)).save(opaque, format="PNG")
    filled = _prepared_pixels(opaque.getvalue())
    assert filled.getpixel((256 - 179, 256)) == (0, 255, 0) and filled.getpixel((256 - 182, 256)) == (255, 255, 255)


def test_fully_transparent_logo_is_rejected():
    data = BytesIO()
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(data, format="PNG")
    with pytest.raises(AppError):
        prepare_logo(data.getvalue())


def test_animated_and_oversized_logos_are_rejected():
    frames = BytesIO()
    Image.new("RGB", (32, 32), "red").save(
        frames, format="WEBP", save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")])
    with pytest.raises(AppError):
        prepare_logo(frames.getvalue())
    with pytest.raises(AppError):
        prepare_logo(b"\x89PNG" + b"0" * MAX_LOGO_BYTES)
    with pytest.raises(AppError):
        prepare_logo(b"not an image")


def test_embed_artwork_keeps_native_modules_and_drops_remote_images(tmp_path):
    native = tmp_path / "native.svg"
    native.write_text('<svg xmlns="http://www.w3.org/2000/svg" '
                      'xmlns:xlink="http://www.w3.org/1999/xlink" width="600" height="600">'
                      '<image xlink:href="https://example.invalid/badge.png"/>'
                      '<circle cx="150" cy="150" r="5" fill="black"/></svg>')
    logo = BytesIO()
    Image.new("RGB", (64, 64), "green").save(logo, format="PNG")
    output = tmp_path / "code.svg"
    embed_artwork(native, output, logo.getvalue(), logo.getvalue())
    text = output.read_text()
    assert "https://example.invalid" not in text
    assert '<circle cx="150" cy="150" r="5" fill="black"' in text
    assert text.count("data:image/png;base64,") == 2


def test_embed_artwork_without_logo_keeps_the_native_badge_only(tmp_path):
    native = tmp_path / "native.svg"
    native.write_text('<svg xmlns="http://www.w3.org/2000/svg" '
                      'xmlns:xlink="http://www.w3.org/1999/xlink" width="600" height="600">'
                      '<image xlink:href="https://example.invalid/badge.png"/>'
                      '<circle cx="150" cy="150" r="5" fill="black"/></svg>')
    badge = BytesIO()
    Image.new("RGB", (64, 64), "green").save(badge, format="PNG")
    output = tmp_path / "code.svg"
    embed_artwork(native, output, badge.getvalue())
    text = output.read_text()
    assert "https://example.invalid" not in text
    assert "center-art-clip" not in text
    assert text.count("data:image/png;base64,") == 1