"""Validate local artwork and embed it without touching native code modules."""
import base64
from io import BytesIO
from pathlib import Path
import warnings
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import AppError

MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_LOGO_DIMENSION = 4096
SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)


def prepare_logo(data: bytes) -> bytes:
    if not data or len(data) > MAX_LOGO_BYTES:
        raise AppError("INVALID_LOGO", "Logo 不能为空且不能超过 2 MiB", 413 if data else 400)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format not in ("PNG", "JPEG", "WEBP"):
                    raise AppError("INVALID_LOGO", "仅支持 PNG、JPEG 或 WebP 图片", 400)
                if max(source.size) > MAX_LOGO_DIMENSION:
                    raise AppError("INVALID_LOGO", "Logo 每边不能超过 4096 像素", 400)
                if getattr(source, "n_frames", 1) != 1:
                    raise AppError("INVALID_LOGO", "请上传静态 Logo，不支持动图", 400)
                # Decoded pixels cost 4 bytes each (64 MiB at 4096px), so avoid full-size copies.
                source.draft(None, (720, 720))  # JPEG only: decode at a reduced DCT scale.
                ImageOps.exif_transpose(source, in_place=True)
                image = source
                if source.mode != "RGBA":
                    image = source.convert("RGBA")
                    source.close()
                bounds = image.getbbox(alpha_only=True)
                if bounds is None:
                    raise AppError("INVALID_LOGO", "Logo 不能是完全透明的图片", 400)
                if bounds != (0, 0, image.width, image.height):
                    image = image.crop(bounds)
                # A 360px square fits inside a 512px circle, retaining all artwork.
                image.thumbnail((360, 360), Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (512, 512), "white")
                canvas.alpha_composite(image, ((512 - image.width) // 2, (512 - image.height) // 2))
                output = BytesIO()
                canvas.convert("RGB").save(output, format="PNG")
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise AppError("INVALID_LOGO", "无法读取 Logo，请上传有效的 PNG、JPEG 或 WebP 图片", 400) from error


def _data_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def embed_artwork(source: Path, destination: Path, badge: bytes, logo: bytes | None = None) -> None:
    root = ET.fromstring(source.read_bytes())
    if root.tag != f"{{{SVG}}}svg" or root.get("width") != "600" or root.get("height") != "600":
        raise AppError("RUNTIME_ERROR", "原生生成器返回了未知图像格式", 503)
    images = list(root.iter(f"{{{SVG}}}image"))
    if len(images) != 1:
        raise AppError("RUNTIME_ERROR", "原生图像模板不匹配", 503)
    images[0].set(f"{{{XLINK}}}href", _data_uri(badge))
    images[0].attrib.pop("href", None)
    root.set("viewBox", "0 0 600 600")
    root.insert(0, ET.Element(f"{{{SVG}}}rect", {
        "x": "0", "y": "0", "width": "600", "height": "600", "fill": "white",
    }))
    # The brand logo is optional: without it the native artwork stays unobstructed.
    if logo is not None:
        definitions = ET.SubElement(root, f"{{{SVG}}}defs")
        clip = ET.SubElement(definitions, f"{{{SVG}}}clipPath", {"id": "center-art-clip"})
        ET.SubElement(clip, f"{{{SVG}}}circle", {"cx": "300", "cy": "300", "r": "130"})
        ET.SubElement(root, f"{{{SVG}}}image", {
            "x": "170", "y": "170", "width": "260", "height": "260",
            "clip-path": "url(#center-art-clip)", f"{{{XLINK}}}href": _data_uri(logo),
        })
    for image in root.iter(f"{{{SVG}}}image"):
        if not image.get(f"{{{XLINK}}}href", "").startswith("data:image/png;base64,"):
            raise AppError("RUNTIME_ERROR", "生成图像仍包含外部资源", 503)
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
