# syntax=docker/dockerfile:1.7
# One image for store-sim, pip-scorer and the end-to-end tests. Build context: repository root.
# Unit tests for sim and scorer run during the build.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /repo
COPY sim/pyproject.toml sim/pyproject.toml
COPY sim/store_sim sim/store_sim
COPY scorer/pyproject.toml scorer/pyproject.toml
COPY scorer/pip_scorer scorer/pip_scorer
COPY tests/requirements.txt tests/requirements.txt
RUN pip install "./sim[test]" "./scorer[test]" -r tests/requirements.txt
COPY config config
COPY schemas schemas
COPY sim sim
COPY scorer scorer
COPY tests tests
# The OpenAPI contract test (milestone E5) needs the committed spec and the comparer.
COPY services/event-core/openapi.yaml services/event-core/openapi.yaml
COPY scripts/openapi_surface.py scripts/openapi_surface.py
RUN python -m pytest -q -p no:cacheprovider sim/tests scorer/tests
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
CMD ["store-sim", "--help"]
