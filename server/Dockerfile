# --- builder ----------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -U pip pdm==2.26.8

WORKDIR /build
COPY server/pyproject.toml server/pdm.lock ./
COPY server/src ./src
COPY docs/renderer-spec.md ./renderer-spec.md

# Resolve deps from lockfile, install via pip into /opt/venv, then install
# the project itself without re-resolving deps.
RUN pdm export --prod --no-hashes -o /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && pip install --no-cache-dir --no-deps .

# --- runtime ----------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    EINK_DATA_DIR=/data

RUN useradd --system --uid 10001 --create-home --home-dir /home/eink eink \
 && mkdir -p /data \
 && chown -R eink:eink /data

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/renderer-spec.md /app/renderer-spec.md
ENV EINK_RENDERER_SPEC_PATH=/app/renderer-spec.md

USER eink
WORKDIR /home/eink
EXPOSE 8989
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8989/healthz', timeout=2).status==200 else 1)"

CMD ["uvicorn", "eink_server.main:app", "--host", "0.0.0.0", "--port", "8989"]
