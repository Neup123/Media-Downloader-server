# Pinned Go toolchain and source release for the get-sauce CLI.
FROM golang:1.27-alpine AS get-sauce-build
ENV GOTOOLCHAIN=local CGO_ENABLED=0
RUN go install github.com/gan-of-culture/get-sauce@v1.10.0 \
    && go version -m /go/bin/get-sauce \
        | awk '$1 == "mod" && $2 == "github.com/gan-of-culture/get-sauce" {print $3}' \
        > /go/get-sauce.version \
    && grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+

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
    && apt-get install -y --no-install-recommends aria2=1.37.0+debian-3 curl ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 downloader \
    && useradd --system --uid 10001 --gid downloader --home-dir /data --shell /usr/sbin/nologin downloader \
    && mkdir -p /app /data \
    && chown -R downloader:downloader /app /data

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY openapi.yaml ./openapi.yaml
COPY --from=web-build /web/dist ./web/dist
COPY --from=get-sauce-build /go/bin/get-sauce /usr/local/bin/get-sauce
# Extracted from the installed Go binary's own build metadata; not a guessed CLI flag.
COPY --from=get-sauce-build /go/get-sauce.version /app/get-sauce.version
RUN chown -R downloader:downloader /app
USER downloader
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8080/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
 /go/get-sauce.version

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
    && apt-get install -y --no-install-recommends aria2=1.37.0+debian-3 curl ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 downloader \
    && useradd --system --uid 10001 --gid downloader --home-dir /data --shell /usr/sbin/nologin downloader \
    && mkdir -p /app /data \
    && chown -R downloader:downloader /app /data

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY openapi.yaml ./openapi.yaml
COPY --from=web-build /web/dist ./web/dist
COPY --from=get-sauce-build /go/bin/get-sauce /usr/local/bin/get-sauce
RUN chown -R downloader:downloader /app
USER downloader
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8080/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
