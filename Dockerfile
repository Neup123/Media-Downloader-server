# Pinned Go toolchain and source release for the get-sauce CLI.
FROM golang:1.27-alpine AS get-sauce-build
ENV GOTOOLCHAIN=local CGO_ENABLED=0
RUN go install github.com/gan-of-culture/get-sauce@v1.10.0
RUN go version -m /go/bin/get-sauce | awk '$1 == "mod" && $2 == "github.com/gan-of-culture/get-sauce" {print $3}' > /go/get-sauce.version && grep -Eq '^v[0-9]+[.][0-9]+[.][0-9]+$' /go/get-sauce.version

# Cobalt runs as a Core-owned subprocess in the runtime container.
FROM node:24-trixie-slim AS cobalt-build
RUN apt-get update && apt-get install --yes --no-install-recommends git python3 make g++ ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/cobalt
RUN git init && git remote add origin https://github.com/imputnet/cobalt.git \
    && git fetch --depth=1 origin a636575b09de1fc55d9b8cd98cac88f5f2f16b42 \
    && git checkout --detach FETCH_HEAD \
    && corepack enable && pnpm install --prod --frozen-lockfile \
    && pnpm deploy --filter=@imput/cobalt-api --prod /opt/cobalt-runtime \
    # Upstream's private ffmpeg tunnel picks a random loopback port. Pin it
    # so the egress guard can permit this one endpoint without opening a range.
    && test "$(grep -c 'port: 0,' /opt/cobalt-runtime/src/core/itunnel.js)" = 1 \
    && sed -i 's/port: 0,/port: 9001,/' /opt/cobalt-runtime/src/core/itunnel.js

# Build the React web UI as static assets.
FROM node:22-alpine AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm test && npm run build

# The service container is self-contained and does not require Nextcloud.
FROM python:3.13-slim-trixie AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    OUTPUT_DIR=/data/downloads \
    DATABASE_URL=sqlite:////data/downloader.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends aria2=1.37.0+debian-3 curl=8.14.1-2+deb13u7 ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 downloader \
    && useradd --system --uid 10001 --gid downloader --home-dir /data --shell /usr/sbin/nologin downloader \
    && mkdir -p /app /data \
    && chown -R downloader:downloader /app /data

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
COPY --from=cobalt-build /usr/local/bin/node /usr/local/bin/node
COPY --from=cobalt-build /opt/cobalt-runtime /opt/cobalt/api
COPY --from=cobalt-build /opt/cobalt/.git /opt/cobalt/.git
COPY app/ ./app/
# Probe shims use importlib.metadata and fail when the installed package is
# missing, rather than reporting a misleading Python interpreter version.
COPY app/engine_probe_cli.py /usr/local/bin/telegram-engine
COPY app/cobalt_probe_cli.py /usr/local/bin/cobalt-engine
RUN chmod 755 /usr/local/bin/telegram-engine \
    && chmod 755 /usr/local/bin/cobalt-engine && cobalt-engine --version \
    && ln -s telegram-engine /usr/local/bin/musicdl-engine \
    && ln -s telegram-engine /usr/local/bin/ddddocr-engine \
    && telegram-engine --version \
    && musicdl-engine --version \
    && ddddocr-engine --version \
    && pyload --version
COPY VERSION ./VERSION
COPY openapi.yaml ./openapi.yaml
COPY --from=web-build /web/dist ./web/dist
COPY --from=get-sauce-build /go/bin/get-sauce /usr/local/bin/get-sauce
# Built from the installed binary's module metadata, not a guessed CLI version.
COPY --from=get-sauce-build /go/get-sauce.version /app/get-sauce.version
RUN chown -R downloader:downloader /app
USER downloader
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8080/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
