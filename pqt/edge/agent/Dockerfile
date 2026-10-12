# syntax=docker/dockerfile:1.7
# PQT edge agent (Tier 2, Project 8). Build context: the pqt/ folder. Runs as a non-root user with a
# read-only root filesystem (compose); state lives in /var/lib/pqt-edge, config and secrets in /config.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PQT_MODEL_DIR=/var/lib/pqt-edge/models
WORKDIR /app
COPY vision/lab/pyproject.toml vision/lab/pyproject.toml
COPY vision/lab/pqt_vision vision/lab/pqt_vision
COPY edge/agent/pyproject.toml edge/agent/pyproject.toml
COPY edge/agent/pqt_edge edge/agent/pqt_edge
RUN pip install ./vision/lab ./edge/agent \
 && useradd --system --uid 10002 --no-create-home edge \
 && mkdir -p /var/lib/pqt-edge && chown 10002 /var/lib/pqt-edge
USER 10002
ENTRYPOINT ["pqt-edge", "run", "--config", "/config/agent.yaml"]
