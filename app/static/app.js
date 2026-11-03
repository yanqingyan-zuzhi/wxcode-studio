(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    'generateForm', 'inputFields', 'url', 'urlError', 'logo', 'logoError',
    'dropZone', 'uploadEmpty', 'chooseLogo', 'replaceLogo', 'removeLogo',
    'logoPreview', 'logoImage', 'logoName', 'logoMeta', 'logoAnnouncement',
    'generateButton', 'generateLabel', 'generateArrow', 'submitNote',
    'formError', 'errorTitle', 'errorMessage', 'outputPanel', 'emptyState',
    'loadingState', 'resultImage', 'resultDetails', 'resultTag', 'encodedUrl',
    'resultDimensions', 'downloadButton', 'resultAnnouncement',
    'serviceStatus', 'serviceText', 'healthRetry'
  ].map((id) => [id, $(id)]));
  const state = {
    file: null, logoUrl: null, resultUrl: null, logoVersion: 0,
    logoJob: null, validating: false, busy: false, failed: false,
    healthController: null, requestController: null, closed: false
  };
  const MAX_LOGO_BYTES = 2 * 1024 * 1024;
  const LOGO_TYPES = new Set(['image/png', 'image/jpeg', 'image/jpg', 'image/webp']);
  const PASTE_EXTENSIONS = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/webp': 'webp' };
  const ERROR_MESSAGES = {
    INVALID_URL: '链接格式不正确，请输入完整的 http 或 https 地址。',
    INVALID_LOGO: 'Logo 无法使用，请更换 PNG、JPEG 或 WebP 图片。',
    CAPACITY_EXCEEDED: '链接超出葵花码容量，请缩短链接后重试。',
    BUSY: '生成服务正忙，请稍后点击重试生成。',
    RUNTIME_ERROR: '原生编码运行异常，请检查服务后重试。',
    PAYLOAD_TOO_LARGE: '上传内容过大，请使用不超过 2 MiB 的 Logo。'
  };

  class DisplayError extends Error {
    constructor(message, code = '') { super(message); this.code = code; }
  }

  function setHealth(kind, text) {
    ui.serviceStatus.dataset.state = kind;
    ui.serviceText.textContent = text;
    ui.healthRetry.hidden = kind !== 'unavailable';
  }

  async function checkHealth() {
    if (state.healthController || state.closed) return;
    const controller = new AbortController();
    state.healthController = controller;
    ui.healthRetry.disabled = true;
    setHealth('checking', '正在连接服务');
    const timer = setTimeout(() => controller.abort(), 6000);
    try {
      const response = await fetch('/api/health', {
        signal: controller.signal, cache: 'no-store', credentials: 'same-origin'
      });
      if (!response.ok) throw new Error('Health unavailable');
      const health = await response.json();
      if (state.closed || state.healthController !== controller) return;
      if (health.status === 'ok' && health.code_type === 'WX_CODE' && health.runtime_ready === true) {
        setHealth('ready', '原生服务就绪');
      } else {
        setHealth('unavailable', '原生环境未就绪');
      }
    } catch {
      if (!state.closed && state.healthController === controller) setHealth('unavailable', '服务未连接');
    } finally {
      clearTimeout(timer);
      if (state.healthController === controller) {
        state.healthController = null;
        ui.healthRetry.disabled = false;
      }
    }
  }

  function setFieldError(field, message) {
    const error = field === 'url' ? ui.urlError : ui.logoError;
    const input = field === 'url' ? ui.url : ui.logo;
    error.textContent = message;
    error.hidden = !message;
    input.setAttribute('aria-invalid', String(Boolean(message)));
    if (field === 'logo') {
      ui.dropZone.classList.toggle('has-error', Boolean(message));
      ui.chooseLogo.setAttribute('aria-invalid', String(Boolean(message)));
    }
  }

  function clearFormError() {
    ui.formError.hidden = true;
    ui.errorMessage.textContent = '';
    state.failed = false;
    if (!state.busy) ui.generateLabel.textContent = '生成葵花码';
  }

  function updateSubmit() {
    ui.generateButton.disabled = state.busy || state.validating;
    if (state.validating) ui.generateLabel.textContent = '正在检查 Logo…';
    else if (state.busy) ui.generateLabel.textContent = '正在生成…';
    else ui.generateLabel.textContent = state.failed ? '重试生成' : '生成葵花码';
  }

  function setBusy(busy) {
    state.busy = busy;
    ui.inputFields.disabled = busy;
    ui.dropZone.classList.toggle('is-disabled', busy);
    ui.outputPanel.setAttribute('aria-busy', String(busy));
    ui.generateArrow.hidden = busy;
    ui.submitNote.textContent = busy
      ? '正在离线编码并进行原生回读，请稍候…'
      : state.failed ? '已保留输入内容，可修改后重试。' : '离线原生编码 · Logo 可选 · 不使用普通二维码替代';
    updateSubmit();
  }

  function clearResult(announce = false) {
    const hadResult = Boolean(state.resultUrl);
    ui.resultImage.hidden = true;
    ui.resultImage.removeAttribute('src');
    ui.resultDetails.hidden = true;
    ui.downloadButton.hidden = true;
    ui.downloadButton.removeAttribute('href');
    ui.encodedUrl.textContent = '';
    if (state.resultUrl) URL.revokeObjectURL(state.resultUrl);
    state.resultUrl = null;
    ui.emptyState.hidden = false;
    ui.loadingState.hidden = true;
    ui.resultTag.dataset.state = 'idle';
    ui.resultTag.textContent = '等待生成';
    if (announce && hadResult) ui.resultAnnouncement.textContent = '输入已更改，请重新生成葵花码。';
  }

  function releaseLogo() {
    state.file = null;
    ui.logoImage.removeAttribute('src');
    if (state.logoUrl) URL.revokeObjectURL(state.logoUrl);
    state.logoUrl = null;
    ui.logoPreview.hidden = true;
    ui.uploadEmpty.hidden = false;
    ui.logoName.textContent = '';
    ui.logoMeta.textContent = '';
    ui.logo.value = '';
  }

  // Cancellable inspection: a newer selection settles and releases the previous image immediately.
  function inspectLogo(file) {
    return new Promise((resolve) => {
      const image = new Image();
      const objectUrl = URL.createObjectURL(file);
      let settled = false;
      const job = { cancel: () => finish(null) };
      const timer = setTimeout(() => finish(null), 10000);
      function finish(dimensions) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        image.onload = image.onerror = null;
        image.src = '';
        URL.revokeObjectURL(objectUrl);
        if (state.logoJob === job) state.logoJob = null;
        resolve(dimensions);
      }
      state.logoJob = job;
      image.onload = () => finish({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => finish(null);
      image.src = objectUrl;
    });
  }

  // Clipboard bitmaps often arrive named "image.png"; give them a recognisable local name.
  function namePastedFile(file) {
    if (file.name && file.name !== 'image.png' && file.name !== 'blob') return file;
    const extension = PASTE_EXTENSIONS[file.type] || 'png';
    try {
      return new File([file], '粘贴的图片.' + extension, { type: file.type || 'image/png', lastModified: Date.now() });
    } catch { return file; }
  }

  async function selectLogo(source, origin = 'file') {
    if (state.busy || !source) return;
    const file = origin === 'paste' ? namePastedFile(source) : source;
    const version = ++state.logoVersion;
    if (state.logoJob) state.logoJob.cancel();
    releaseLogo();
    clearResult(true);
    clearFormError();
    setFieldError('logo', '');
    state.validating = false;
    let message = '';
    if (!LOGO_TYPES.has(file.type)) message = '请选择 PNG、JPEG 或 WebP 格式的图片（也支持直接粘贴截图），不支持 SVG、GIF 等格式。';
    else if (!file.size) message = '图片是空文件，请重新选择。';
    else if (file.size > MAX_LOGO_BYTES) message = '图片超过 2 MiB，请压缩后重新上传。';
    if (message) {
      setFieldError('logo', message);
      ui.logoAnnouncement.textContent = message;
      updateSubmit();
      if (document.activeElement === ui.replaceLogo) ui.chooseLogo.focus();
      return;
    }
    state.validating = true;
    ui.logoAnnouncement.textContent = '正在检查 Logo 图片。';
    updateSubmit();
    try {
      const dimensions = await inspectLogo(file);
      if (state.closed || version !== state.logoVersion) return;
      if (!dimensions || !dimensions.width || !dimensions.height) {
        throw new DisplayError('无法读取图片，请选择有效的 PNG、JPEG 或 WebP 文件。');
      }
      if (dimensions.width > 4096 || dimensions.height > 4096) {
        throw new DisplayError('图片宽度和高度均不能超过 4096 像素，请缩小后重新上传。');
      }
      state.logoUrl = URL.createObjectURL(file);
      state.file = file;
      ui.logoImage.src = state.logoUrl;
      ui.logoName.textContent = file.name;
      ui.logoMeta.textContent = `${dimensions.width} × ${dimensions.height} px · ${Math.max(1, Math.round(file.size / 1024))} KiB`;
      ui.uploadEmpty.hidden = true;
      ui.logoPreview.hidden = false;
      ui.logoAnnouncement.textContent = origin === 'paste'
        ? '已从剪贴板添加 Logo：' + file.name : 'Logo 已就绪：' + file.name;
      if ([ui.chooseLogo, ui.logo, ui.replaceLogo].includes(document.activeElement)) ui.replaceLogo.focus();
    } catch (error) {
      if (state.closed || version !== state.logoVersion) return;
      const message = error instanceof DisplayError ? error.message : '无法读取图片，请重新选择。';
      setFieldError('logo', message);
      ui.logoAnnouncement.textContent = message;
      if (document.activeElement === ui.replaceLogo) ui.chooseLogo.focus();
    } finally {
      if (!state.closed && version === state.logoVersion) {
        state.validating = false;
        updateSubmit();
      }
    }
  }

  function validHttpUrl(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && Boolean(url.hostname);
    } catch { return false; }
  }

  function readPayload(headers) {
    if (headers.get('X-Code-Type') !== 'WX_CODE' ||
        headers.get('X-Verification') !== 'native-roundtrip' ||
        headers.get('X-WeChat-Verified') !== 'false') {
      throw new DisplayError('返回结果缺少约定的 WX_CODE 原生回读信息，未展示为成功。请检查生成服务。');
    }
    let payload = '';
    try { payload = atob(headers.get('X-Payload-Base64') || ''); } catch { /* Rejected below. */ }
    if (!payload || /[^\x21-\x7e]/.test(payload) || !validHttpUrl(payload)) {
      throw new DisplayError('服务没有返回有效的实际编码地址，请检查生成服务后重试。');
    }
    return payload;
  }

  function decodeResult(objectUrl, signal) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      let settled = false;
      const timer = setTimeout(() => finish(new DisplayError('生成图片无法解码，请重试。')), 10000);
      const onAbort = () => finish(new DOMException('Aborted', 'AbortError'));
      function finish(error, dimensions) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal.removeEventListener('abort', onAbort);
        image.onload = image.onerror = null;
        image.src = '';
        if (error) reject(error); else resolve(dimensions);
      }
      image.onload = () => {
        if (!image.naturalWidth || !image.naturalHeight) finish(new DisplayError('生成图片内容无效，请重试。'));
        else finish(null, { width: image.naturalWidth, height: image.naturalHeight });
      };
      image.onerror = () => finish(new DisplayError('生成图片无法解码，请检查服务后重试。'));
      signal.addEventListener('abort', onAbort, { once: true });
      if (signal.aborted) onAbort(); else image.src = objectUrl;
    });
  }

  async function generate(event) {
    event.preventDefault();
    if (state.busy || state.validating || state.closed) return;
    clearFormError();
    const inputUrl = ui.url.value.trim();
    const urlError = validHttpUrl(inputUrl) ? '' : '请输入完整的 http 或 https 链接，例如 https://example.com。';
    setFieldError('url', urlError);
    setFieldError('logo', '');
    if (urlError) {
      ui.url.focus();
      return;
    }
    const body = new FormData();
    body.append('url', inputUrl);
    if (state.file) body.append('logo', state.file, state.file.name || 'logo.png');
    const controller = new AbortController();
    state.requestController = controller;
    let timedOut = false;
    let candidateUrl = null;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 90000);
    clearResult();
    ui.emptyState.hidden = true;
    ui.loadingState.hidden = false;
    ui.resultTag.dataset.state = 'loading';
    ui.resultTag.textContent = '生成中';
    ui.resultAnnouncement.textContent = '正在生成葵花码并进行原生回读校验。';
    setBusy(true);
    try {
      const response = await fetch('/api/generate', {
        method: 'POST', body, signal: controller.signal, credentials: 'same-origin', cache: 'no-store'
      });
      if (!response.ok) {
        let detail = null;
        try { detail = (await response.json()).error; } catch { /* A proxy may return HTML. */ }
        const code = typeof detail?.code === 'string' ? detail.code : '';
        if (response.status === 503) setHealth('unavailable', code === 'BUSY' ? '服务繁忙' : '生成暂不可用');
        const fallback = response.status === 503 ? ERROR_MESSAGES.BUSY
          : response.status === 413 ? ERROR_MESSAGES.PAYLOAD_TOO_LARGE
          : `生成服务返回异常（HTTP ${response.status}），请稍后重试。`;
        throw new DisplayError(typeof detail?.message === 'string' && detail.message.trim()
          ? detail.message : ERROR_MESSAGES[code] || fallback, code);
      }
      if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'image/png') {
        throw new DisplayError('服务返回的不是 PNG 图片，未展示结果。请检查服务后重试。');
      }
      const payload = readPayload(response.headers);
      const blob = await response.blob();
      const signature = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
      if (![137, 80, 78, 71, 13, 10, 26, 10].every((byte, i) => signature[i] === byte)) {
        throw new DisplayError('服务返回的图片不是有效 PNG，未展示结果。请重试。');
      }
      candidateUrl = URL.createObjectURL(blob);
      const dimensions = await decodeResult(candidateUrl, controller.signal);
      if (state.closed || controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      state.resultUrl = candidateUrl;
      candidateUrl = null;
      ui.resultImage.src = state.resultUrl;
      ui.resultImage.hidden = false;
      ui.loadingState.hidden = true;
      ui.resultDetails.hidden = false;
      // The returned ASCII payload is text only, never markup or a navigable link.
      ui.encodedUrl.textContent = payload;
      ui.resultDimensions.textContent = `${dimensions.width} × ${dimensions.height} px · PNG`;
      ui.downloadButton.href = state.resultUrl;
      ui.downloadButton.hidden = false;
      ui.resultTag.dataset.state = 'success';
      ui.resultTag.textContent = '已生成';
      ui.resultAnnouncement.textContent = '葵花码已生成，原生回读已校验。可下载 PNG；微信手机实扫仍需自行测试。';
      if (state.healthController) {
        state.healthController.abort();
        state.healthController = null;
        ui.healthRetry.disabled = false;
      }
      setHealth('ready', '原生服务就绪');
    } catch (error) {
      if (state.closed) return;
      clearResult();
      state.failed = true;
      ui.resultTag.dataset.state = 'error';
      ui.resultTag.textContent = '生成失败';
      ui.formError.hidden = false;
      ui.errorTitle.textContent = '这次没能生成，请重试';
      const message = timedOut ? '生成等待超时，请稍后点击重试生成。'
        : error instanceof DisplayError ? error.message
        : '无法连接生成服务。请确认服务已启动、网络连接正常，然后重试。';
      ui.errorMessage.textContent = message;
      ui.resultAnnouncement.textContent = '生成失败，未产生可下载结果。';
      if (!(error instanceof DisplayError)) setHealth('unavailable', '服务未连接');
      if (error.code === 'INVALID_URL') setFieldError('url', message);
      if (error.code === 'INVALID_LOGO') setFieldError('logo', message);
    } finally {
      clearTimeout(timer);
      if (candidateUrl) URL.revokeObjectURL(candidateUrl);
      if (state.requestController === controller) {
        state.requestController = null;
        if (!state.closed) setBusy(false);
      }
    }
  }

  ui.generateForm.addEventListener('submit', generate);
  ui.healthRetry.addEventListener('click', checkHealth);
  ui.chooseLogo.addEventListener('click', () => ui.logo.click());
  ui.replaceLogo.addEventListener('click', () => ui.logo.click());
  ui.logo.addEventListener('change', () => {
    const file = ui.logo.files?.[0];
    if (file) void selectLogo(file);
    ui.logo.value = '';
  });
  ui.removeLogo.addEventListener('click', () => {
    if (state.busy) return;
    ++state.logoVersion;
    if (state.logoJob) state.logoJob.cancel();
    state.validating = false;
    releaseLogo();
    clearResult(true);
    clearFormError();
    setFieldError('logo', '');
    updateSubmit();
    ui.logoAnnouncement.textContent = 'Logo 已移除，将生成不带 Logo 的葵花码。';
    ui.chooseLogo.focus();
  });
  ui.url.addEventListener('input', () => {
    if (state.busy) return;
    setFieldError('url', '');
    clearFormError();
    clearResult(true);
  });

  function clipboardImage(event) {
    const data = event.clipboardData;
    if (!data) return null;
    for (const item of Array.from(data.items || [])) {
      if (item.kind !== 'file' || !item.type.startsWith('image/')) continue;
      const file = item.getAsFile();
      if (file) return file;
    }
    return Array.from(data.files || []).find((file) => file.type.startsWith('image/')) || null;
  }

  // Paste anywhere: screenshots are the common case, while text pastes in fields stay untouched.
  document.addEventListener('paste', (event) => {
    if (state.busy || state.closed) return;
    const file = clipboardImage(event);
    if (!file) return;
    const active = document.activeElement;
    const editing = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement ||
      Boolean(active && active.isContentEditable);
    const hasText = Array.from(event.clipboardData?.types || []).includes('text/plain');
    if (editing && hasText) return;
    event.preventDefault();
    void selectLogo(file, 'paste');
  });

  let dragDepth = 0;
  const hasFiles = (event) => Array.from(event.dataTransfer?.types || []).includes('Files');
  ui.dropZone.addEventListener('dragenter', (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    if (!state.busy && ++dragDepth > 0) ui.dropZone.classList.add('is-dragging');
  });
  ui.dropZone.addEventListener('dragover', (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = state.busy ? 'none' : 'copy';
  });
  ui.dropZone.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) ui.dropZone.classList.remove('is-dragging');
  });
  ui.dropZone.addEventListener('drop', (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    ui.dropZone.classList.remove('is-dragging');
    if (state.busy) return;
    const files = event.dataTransfer.files;
    if (files.length !== 1) {
      setFieldError('logo', '每次请选择一张 Logo 图片。');
      ui.logoAnnouncement.textContent = '每次请选择一张 Logo 图片。';
    } else void selectLogo(files[0]);
  });
  // Prevent an accidental file drop outside the zone from navigating away from this tool.
  for (const type of ['dragover', 'drop']) document.addEventListener(type, (event) => {
    if (hasFiles(event)) event.preventDefault();
  });
  window.addEventListener('pagehide', (event) => {
    // A bfcache entry owns its previews until restored or discarded by the browser.
    if (event.persisted) return;
    state.closed = true;
    ++state.logoVersion;
    if (state.logoJob) state.logoJob.cancel();
    state.healthController?.abort();
    state.requestController?.abort();
    if (state.logoUrl) URL.revokeObjectURL(state.logoUrl);
    if (state.resultUrl) URL.revokeObjectURL(state.resultUrl);
  });

  void checkHealth();
})();
