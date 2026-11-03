"""Pinned, offline native generation with mandatory exact-payload roundtrips."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Protocol

from .errors import AppError
from .images import embed_artwork
from .validation import decode_payload, normalize_url

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedImage:
    png: bytes
    payload: str


class CodeService(Protocol):
    def check(self) -> None: ...
    def generate(self, url: str, logo_png: bytes | None) -> GeneratedImage: ...


class Generator:
    def __init__(self) -> None:
        self.runtime = Path(os.environ.get("WXCODE_RUNTIME_DIR", ROOT / "vendor/qbar")).resolve()
        self.jar = Path(os.environ.get("WXCODE_BRIDGE_JAR", ROOT / "native/target/wxcode-bridge.jar")).resolve()
        self.sandbox = ROOT / "app/sandbox.py"
        self.temporary = Path(os.environ.get("WXCODE_TMP_DIR", os.environ.get("PI_SCRATCH_DIR", tempfile.gettempdir())))
        self.badge = (ROOT / "app/assets/miniapp-badge.png").read_bytes()
        self.java = shutil.which("java") or ""
        self.renderer = shutil.which("rsvg-convert") or ""
        if sys.platform != "linux" or platform.machine() != "x86_64":
            raise AppError("RUNTIME_ERROR", "当前部署仅支持 Linux amd64", 503)
        if not self.jar.is_file() or not self.java or not self.renderer:
            raise AppError("RUNTIME_ERROR", "缺少 JAR、Java 或 librsvg，请先完成部署准备", 503)
        manifest = json.loads((ROOT / "runtime.lock.json").read_text())
        for item in manifest["files"]:
            path = self.runtime / item["target"]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise AppError("RUNTIME_ERROR", "原生库或模型缺失/校验失败，请运行依赖准备脚本", 503)
        self.temporary.mkdir(parents=True, exist_ok=True)

    def _run(self, command: list[str], work: Path, *, models: bool = False, timeout: int = 90) -> str:
        env = dict(os.environ, QBAR_WITH_MODELS="1" if models else "0")
        try:
            result = subprocess.run([sys.executable, str(self.sandbox), *command],
                                    cwd=work, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AppError("RUNTIME_ERROR", "本地生成进程超时或无法启动，请稍后重试", 503) from error
        log = result.stdout.decode("utf-8", "replace")
        if result.returncode != 0:
            # Never expose native register dumps, file paths, or submitted URLs.
            LOGGER.error("Offline child failed (exit=%s)", result.returncode)
            if "Encoder did not return a nonempty output vector" in log:
                raise AppError("CAPACITY_EXCEEDED", "链接超过当前葵花码可用容量，请缩短链接后重试", 422)
            raise AppError("RUNTIME_ERROR", "本地编解码失败，未返回未校验的图片", 503)
        return log

    def _native(self, arguments: list[str], work: Path, *, models: bool = False) -> str:
        java_tmp = work / "jvm"
        java_tmp.mkdir(exist_ok=True)
        # Short-lived JVM: most of its memory is the native emulator, not the Java heap.
        return self._run([self.java, "--enable-native-access=ALL-UNNAMED", "-Xmx48m",
                          "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1",
                          f"-Djava.io.tmpdir={java_tmp}", "-Djava.awt.headless=true",
                          "-Dorg.slf4j.simpleLogger.defaultLogLevel=error", "-jar", str(self.jar),
                          str(self.runtime / "lib"), *arguments], work, models=models)

    def check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wxcode-check-", dir=self.temporary) as directory:
            log = self._native([], Path(directory))
            if "QBAR_VERSION=3.2.20190712" not in log:
                raise AppError("RUNTIME_ERROR", "原生库版本或运行环境不匹配", 503)

    def decode(self, image: Path) -> bytes:
        with tempfile.TemporaryDirectory(prefix="wxcode-read-", dir=self.temporary) as directory:
            return decode_payload(self._native(["decode", str(image.resolve())], Path(directory), models=True))

    def generate(self, url: str, logo_png: bytes | None = None) -> GeneratedImage:
        payload = normalize_url(url)
        with tempfile.TemporaryDirectory(prefix="wxcode-", dir=self.temporary) as directory:
            work = Path(directory)
            raw, svg, png = (work / name for name in ("native.svg", "code.svg", "code.png"))
            self._native(["encode", payload, "18", "0", "3", str(raw)], work)
            embed_artwork(raw, svg, self.badge, logo_png)
            self._run([self.renderer, str(svg), "-o", str(png)], work, timeout=30)
            actual = decode_payload(self._native(["decode", str(png)], work, models=True))
            if actual != payload.encode("ascii"):
                raise AppError("RUNTIME_ERROR", "图片未通过原生回读校验，未返回生成结果", 503)
            return GeneratedImage(png=png.read_bytes(), payload=payload)
