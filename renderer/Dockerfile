# --- builder ----------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -U pip pdm==2.26.8

WORKDIR /build
COPY renderer/pyproject.toml renderer/pdm.lock ./
COPY renderer/src ./src

RUN pdm export --prod --no-hashes -o /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt \
 && pip install --no-cache-dir --no-deps .

# --- runtime ----------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    TZ=Europe/Berlin

# tzdata, because the header states the period in Berlin time. Without the
# zone data zoneinfo would fall back to UTC and the date would roll over an
# hour early in summer.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10002 --create-home --home-dir /home/render render

COPY --from=builder /opt/venv /opt/venv

USER render
WORKDIR /home/render

ENTRYPOINT ["eink-render"]
