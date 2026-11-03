"""Single-process, same-origin web application. Uploads/results are not persisted."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator, Awaitable, Callable, MutableMapping

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from .errors import AppError
from .generator import CodeService, GeneratedImage, Generator
from .images import MAX_LOGO_BYTES, prepare_logo
from .validation import normalize_url

STATIC = Path(__file__).with_name("static")
MAX_REQUEST_BYTES = 3 * 1024 * 1024


def error_response(error: AppError) -> JSONResponse:
    return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status,
                        headers={"Cache-Control": "no-store"})


class BodyLimitMiddleware:
    """Bound multipart bodies including requests without Content-Length."""
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                await error_response(AppError("PAYLOAD_TOO_LARGE", "请求过大，图片最大为 2 MiB", 413))(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        sent = False

        async def replay() -> MutableMapping[str, object]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(service: CodeService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active = service if service is not None else Generator()
        await asyncio.to_thread(active.check)
        application.state.generator = active
        application.state.slot = asyncio.Semaphore(1)
        yield

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(BodyLimitMiddleware)
    application.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")

    @application.middleware("http")
    async def response_headers(request: Request,
                               call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    @application.exception_handler(AppError)
    async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
        return error_response(error)

    @application.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError) -> JSONResponse:
        locations = {item["loc"][-1] for item in error.errors()}
        if "url" not in locations and "logo" in locations:
            return error_response(AppError("INVALID_LOGO", "Logo 上传格式无效，请重新选择图片", 400))
        return error_response(AppError("INVALID_URL", "请输入完整链接", 400))

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @application.get("/api/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "runtime_ready": True, "code_type": "WX_CODE", "wechat_scan_verified": False}

    @application.post("/api/generate")
    async def generate(request: Request, url: Annotated[str, Form()],
                       logo: Annotated[UploadFile | None, File()] = None) -> Response:
        payload = normalize_url(url)
        slot = request.app.state.slot
        try:
            await asyncio.wait_for(slot.acquire(), timeout=0.1)
        except TimeoutError:
            response = error_response(AppError("BUSY", "正在处理其他生成任务，请稍后重试", 503))
            response.headers["Retry-After"] = "3"
            return response
        try:
            raw = b""
            if logo is not None:
                raw = await logo.read(MAX_LOGO_BYTES + 1)
                await logo.close()
            active: CodeService = request.app.state.generator

            def job() -> GeneratedImage:
                prepared = prepare_logo(raw) if raw else None
                return active.generate(payload, prepared)

            task = asyncio.create_task(asyncio.to_thread(job))
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Keep the slot until the child process has really stopped.
                await task
                raise
            return Response(result.png, media_type="image/png", headers={
                "Content-Disposition": 'attachment; filename="wxcode.png"',
                "Cache-Control": "no-store",
                "X-Payload-Base64": base64.b64encode(result.payload.encode("ascii")).decode("ascii"),
                "X-Code-Type": "WX_CODE", "X-Verification": "native-roundtrip", "X-WeChat-Verified": "false",
            })
        finally:
            slot.release()

    return application


app = create_app()
