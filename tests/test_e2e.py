"""Real-browser end-to-end check of upload, generation, and download."""
import base64
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
from PIL import Image

pytestmark = pytest.mark.e2e


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def logo_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (512, 512), (0, 121, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_generate_flow_in_browser(tmp_path: Path):
    from playwright.sync_api import sync_playwright

    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--port", str(port)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                if server.poll() is not None:
                    raise AssertionError("Server exited: " + server.stdout.read().decode("utf-8", "replace")[-2000:])
                time.sleep(0.5)
        else:
            raise AssertionError("Server did not start in time")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector("#serviceStatus[data-state='ready']", timeout=120_000)
            page.fill("#url", "https://example.com/")
            page.set_input_files("#logo", files=[{"name": "logo.png", "mimeType": "image/png",
                                                 "buffer": logo_bytes()}])
            page.click("#generateButton")
            page.wait_for_selector("#resultImage:not([hidden])", timeout=180_000)
            assert page.inner_text("#encodedUrl") == "https://example.com/"
            assert page.get_attribute("#resultImage", "src").startswith("blob:")
            download = page.expect_download()
            with page.expect_download() as download_info:
                page.click("#downloadButton")
            saved = tmp_path / "download.png"
            download_info.value.save_as(saved)
            with Image.open(saved) as image:
                assert image.format == "PNG" and image.size == (600, 600)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=30)


def test_optional_logo_and_clipboard_paste():
    from playwright.sync_api import sync_playwright

    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--port", str(port)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                if server.poll() is not None:
                    raise AssertionError("Server exited: " + server.stdout.read().decode("utf-8", "replace")[-2000:])
                time.sleep(0.5)
        else:
            raise AssertionError("Server did not start in time")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector("#serviceStatus[data-state='ready']", timeout=120_000)
            assert page.get_attribute("#logo", "required") is None
            assert "粘贴" in page.inner_text("#logoHint")

            # A code can be generated with the URL alone.
            page.fill("#url", "https://example.com/")
            page.click("#generateButton")
            page.wait_for_selector("#resultImage:not([hidden])", timeout=180_000)
            assert page.inner_text("#encodedUrl") == "https://example.com/"

            # A pasted bitmap is accepted anywhere on the page, even unnamed clipboard data.
            page.evaluate(
                """(encoded) => {
                  const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
                  const file = new File([bytes], 'image.png', { type: 'image/png' });
                  const data = new DataTransfer();
                  data.items.add(file);
                  document.dispatchEvent(new ClipboardEvent('paste', {
                    clipboardData: data, bubbles: true, cancelable: true,
                  }));
                }""",
                base64.b64encode(logo_bytes()).decode("ascii"),
            )
            page.wait_for_selector("#logoPreview:not([hidden])")
            assert page.inner_text("#logoName") == "粘贴的图片.png"

            page.click("#generateButton")
            page.wait_for_function(
                "document.querySelector('#resultTag').dataset.state === 'success'", timeout=180_000)
            assert page.inner_text("#encodedUrl") == "https://example.com/"
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=30)