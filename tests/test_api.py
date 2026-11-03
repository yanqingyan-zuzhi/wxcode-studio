"""HTTP contract tests with a stub generator; no native library required."""
import base64
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.errors import AppError
from app.generator import GeneratedImage
from app.server import create_app

PNG = b"\x89PNG\r\n\x1a\nstub"


def png_bytes(size=(64, 64), colour="red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


class StubGenerator:
    payload = "https://example.com/"

    def check(self) -> None:
        pass

    def generate(self, url: str, logo_png: bytes | None = None) -> GeneratedImage:
        if url.endswith("/long"):
            raise AppError("CAPACITY_EXCEEDED", "链接超过当前葵花码可用容量，请缩短链接后重试", 422)
        assert logo_png is None or logo_png.startswith(b"\x89PNG\r\n\x1a\n")
        return GeneratedImage(png=PNG, payload=self.payload)


@pytest.fixture
def client() -> TestClient:
    # Entering the context manager runs startup (and therefore the readiness check).
    with TestClient(create_app(StubGenerator())) as active:
        yield active


def post(client: TestClient, url="https://example.com/", logo=None,
         filename="logo.png", content_type="image/png"):
    return client.post("/api/generate", data={"url": url},
                       files={"logo": (filename, logo if logo is not None else png_bytes(), content_type)})


def test_health_reports_offline_runtime_and_unverified_scan(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["code_type"] == "WX_CODE" and body["runtime_ready"] is True
    assert body["wechat_scan_verified"] is False


def test_index_and_static_assets_are_served(client: TestClient):
    assert client.get("/").status_code == 200
    static = client.get("/static/app.js")
    assert static.status_code == 200 and "api/generate" in static.text


def test_generate_returns_png_with_verification_headers(client: TestClient):
    response = post(client)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG
    assert response.headers["x-verification"] == "native-roundtrip"
    assert response.headers["x-wechat-verified"] == "false"
    assert response.headers["x-code-type"] == "WX_CODE"
    assert base64.b64decode(response.headers["x-payload-base64"]) == b"https://example.com/"
    assert "no-store" in response.headers["cache-control"]


def test_invalid_url_returns_a_stable_error_code(client: TestClient):
    assert post(client, url="javascript:alert(1)").json()["error"]["code"] == "INVALID_URL"
    response = client.post("/api/generate", data={"url": "javascript:alert(1)"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "INVALID_URL"


def test_brand_logo_is_optional(client: TestClient):
    response = client.post("/api/generate", data={"url": "https://example.com/"})
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["x-verification"] == "native-roundtrip"


def test_empty_logo_upload_is_treated_as_absent(client: TestClient):
    response = post(client, logo=b"")
    assert response.status_code == 200 and response.content == PNG


def test_capacity_error_is_reported_without_fake_image(client: TestClient):
    response = post(client, url="https://example.com/long")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CAPACITY_EXCEEDED"
    assert response.headers["content-type"].startswith("application/json")


def test_oversized_body_is_rejected_before_generation(client: TestClient):
    response = post(client, logo=b"\x89PNG" + b"0" * (3 * 1024 * 1024))
    assert response.status_code == 413 and response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_logo_above_two_mib_is_rejected(client: TestClient):
    response = post(client, logo=b"\x89PNG" + b"0" * (2 * 1024 * 1024 + 1))
    assert response.status_code == 413 and response.json()["error"]["code"] == "INVALID_LOGO"


def test_unsupported_upload_type_is_rejected(client: TestClient):
    response = post(client, logo=b"GIF89a", filename="logo.gif", content_type="image/gif")
    assert response.status_code == 400 and response.json()["error"]["code"] == "INVALID_LOGO"


def test_security_headers_are_present_on_frontend(client: TestClient):
    headers = client.get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_startup_fails_closed_when_runtime_is_broken():
    class Broken(StubGenerator):
        def check(self) -> None:
            raise AppError("RUNTIME_ERROR", "no runtime", 503)

    # A broken runtime must refuse to start rather than serve unverified images.
    with pytest.raises(AppError):
        with TestClient(create_app(Broken())):
            pass