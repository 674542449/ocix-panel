FROM python:3.11-slim

# 基于 Debian slim，天然兼容 Ubuntu/Debian 宿主机
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# 构建上下文是仓库根目录。依赖里含 Oracle 官方 oci Python SDK。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ocix/ ./ocix/
COPY VERSION ./VERSION
COPY VERSION ./ocix/VERSION

# 前端依赖（Vue / Element Plus / axios）在构建期下载到包内 web/assets，
# 运行时不依赖 unpkg 等 CDN（国内浏览器常无法访问）。
# 依次尝试 unpkg / jsdelivr / npmmirror 三个源，任一可通即可。
RUN set -e; \
    A=/app/ocix/web/assets; mkdir -p "$A"; \
    dl() { \
      out="$1"; shift; \
      for u in "$@"; do \
        if curl -fsSL "$u" -o "$out"; then echo "  ok: $u"; return 0; fi; \
        echo "  fail: $u"; \
      done; \
      echo "  ALL MIRRORS FAILED for $out"; return 1; \
    }; \
    dl "$A/vue.global.prod.js" \
       https://unpkg.com/vue@3/dist/vue.global.prod.js \
       https://cdn.jsdelivr.net/npm/vue@3/dist/vue.global.prod.js \
       https://registry.npmmirror.com/vue/3/files/dist/vue.global.prod.js; \
    dl "$A/element-plus.full.min.js" \
       https://unpkg.com/element-plus/dist/index.full.min.js \
       https://cdn.jsdelivr.net/npm/element-plus/dist/index.full.min.js \
       https://registry.npmmirror.com/element-plus/files/dist/index.full.min.js; \
    dl "$A/element-plus.css" \
       https://unpkg.com/element-plus/dist/index.css \
       https://cdn.jsdelivr.net/npm/element-plus/dist/index.css \
       https://registry.npmmirror.com/element-plus/files/dist/index.css; \
    dl "$A/element-plus-dark.css" \
       https://unpkg.com/element-plus/theme-chalk/dark/css-vars.css \
       https://cdn.jsdelivr.net/npm/element-plus/theme-chalk/dark/css-vars.css \
       https://registry.npmmirror.com/element-plus/files/theme-chalk/dark/css-vars.css; \
    dl "$A/axios.min.js" \
       https://unpkg.com/axios/dist/axios.min.js \
       https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js \
       https://registry.npmmirror.com/axios/files/dist/axios.min.js; \
    ls -lh "$A"

RUN mkdir -p /app/data/keys && chmod 700 /app/data/keys

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "ocix.main:app", "--host", "0.0.0.0", "--port", "8000"]
