# 葵花码工坊

输入网址，可选上传或粘贴 Logo，离线生成葵花码 PNG。

<img src="examples/alipay.png" alt="生成示例" width="240">

- **完全离线：** 不调用微信接口，不需要 AppID/AppSecret，生成过程禁止联网。
- **Logo 可选：** 不传 Logo 也能生成；支持拖拽、选择文件，或直接粘贴截图。
- **结果可靠：** 每张图都用原生解码器回读，链接完全一致才返回。
- **不留数据：** 不保存上传内容和生成结果，也不做短链。

> **注意：** 目前只验证了离线编码和原生回读，**尚未用手机微信实扫验收**，请先自行扫码确认。图形码容量有限，链接过长时会直接报错，不会截断。

## 快速开始

需要 Docker 和 512 MB 可用内存，仅支持 Linux amd64。

```bash
docker compose up -d --build
```

等 `docker compose ps` 显示 `(healthy)` 后，打开 <http://localhost:8000>，填入链接（可上传或粘贴 Logo），点击“生成”，即可预览并下载 600×600 的 PNG。

换端口：

```bash
WXCODE_PORT=9000 docker compose up -d --build
```

## 不用 Docker 运行

需要 Java 17+、Maven、`rsvg-convert`（`librsvg2-bin`）和 `libseccomp2`。

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_runtime.py                  # 下载固定版本的运行时文件并校验
mvn -f native/pom.xml -DskipTests package
MALLOC_ARENA_MAX=1 MALLOC_TRIM_THRESHOLD_=65536 uvicorn app.server:app --port 8000
```

## 接口

```bash
curl -o code.png \
  -F 'url=https://www.alipay.com/' \
  -F 'logo=@examples/alipay-logo.png;type=image/png' \
  http://localhost:8000/api/generate
```

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| POST | `/api/generate` | `multipart/form-data`，字段为 `url`（必填）和 `logo`（可选），成功返回 `image/png` |

不传 `logo` 时生成不带 Logo 的葵花码。失败时返回 `{"error": {"code", "message"}}`：

| 错误码 | 状态码 | 含义 |
|---|---|---|
| `INVALID_URL` | 400 | 链接无效 |
| `INVALID_LOGO` | 400 / 413 | Logo 无效或过大 |
| `PAYLOAD_TOO_LARGE` | 413 | 请求体过大 |
| `CAPACITY_EXCEEDED` | 422 | 链接超出图形码容量 |
| `BUSY` | 503 | 正在处理其他任务，同一时间只生成一张 |
| `RUNTIME_ERROR` | 503 | 本地生成或回读校验失败 |

## 使用限制

- **链接：** 仅支持 http/https，最多 512 字节；实际可用容量更小，取决于链接内容。
- **Logo（可选）：** PNG、JPEG 或 WebP 静态图，不超过 2 MiB，每边不超过 4096 像素；可在页面中直接粘贴截图。

## 测试

```bash
pip install -r requirements-dev.txt
playwright install chromium
python -m pytest
```
