# syntax=docker/dockerfile:1.7
# Browser end-to-end test runner (milestones C2, C3). Build context: repository root (pqt/).

# axe-core for the accessibility test (milestone C3). MPL-2.0, test-only: it is injected into the
# page under test and never shipped to users. Pinned; fetched at build time, not at test time.
FROM node:22-alpine AS axe
WORKDIR /axe
RUN npm install --no-audit --no-fund --ignore-scripts axe-core@4.13.0 \
 && cp node_modules/axe-core/axe.min.js node_modules/axe-core/LICENSE . \
 && rm -rf node_modules package.json package-lock.json

# The base image ships Chromium and its system libraries; keep the tag in step with
# playwright in tests/ui/requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp
COPY tests/ui/requirements.txt /tmp/ui-requirements.txt
RUN python3 -m pip install --break-system-packages -r /tmp/ui-requirements.txt \
 && python3 -c "import playwright, pytest"
WORKDIR /repo
COPY --from=axe /axe /opt/axe
ENV PQT_AXE_JS=/opt/axe/axe.min.js
COPY tests/ui tests/ui
# Compose runs it as the host user (PQT_UID) so reports and video land in .work with the right owner.
USER pwuser
CMD ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/ui"]
