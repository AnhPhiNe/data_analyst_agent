# Tabular Analytics Agent
#
#   Run the app:     docker build --target runtime -t tabular-analytics-agent .
#                    docker run --rm -p 8501:8501 --env-file .env -v taa-data:/data tabular-analytics-agent
#   Verify release:  docker build --target check .
#
# The check target fails the build unless linting, formatting, strict type checking, and the full
# test suite with its coverage threshold pass inside a clean Linux environment.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install pinned dependencies before copying the rest of the source, so code edits reuse this layer.
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
RUN python -m pip install --constraint constraints.txt .

FROM base AS check

# An editable install matches the documented development setup that the checks were verified with.
RUN python -m pip install --constraint constraints.txt --editable ".[dev]"
COPY streamlit_app.py CONTEXT.md Spec.md .env.example ./
COPY tests ./tests
COPY docs ./docs
RUN python -m ruff check . \
    && python -m ruff format --check . \
    && python -m mypy \
    && python -m pytest -q -p no:cacheprovider

FROM base AS runtime

COPY streamlit_app.py ./
COPY .streamlit/config.toml ./.streamlit/config.toml
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown app:app /data
USER app

# Session files, checkpoints, and artifacts live in a volume, never inside the image.
ENV TABULAR_AGENT_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"

CMD ["python", "-m", "streamlit", "run", "streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
