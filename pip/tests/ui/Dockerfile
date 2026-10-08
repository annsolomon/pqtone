# syntax=docker/dockerfile:1.7
# Browser end-to-end test runner (milestone C2). Build context: repository root (pip/).
# The base image ships Chromium and its system libraries; keep the tag in step with
# playwright in tests/ui/requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp
COPY tests/ui/requirements.txt /tmp/ui-requirements.txt
RUN python3 -m pip install --break-system-packages -r /tmp/ui-requirements.txt \
 && python3 -c "import playwright, pytest"
WORKDIR /repo
COPY tests/ui tests/ui
# Compose runs it as the host user (PIP_UID) so reports and video land in .work with the right owner.
USER pwuser
CMD ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/ui"]
