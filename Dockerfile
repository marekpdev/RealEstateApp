# ---------------------------------------------------------------------------
# Stage 1 - builder: has the package manager and resolves/installs the
# locked production dependencies into a self-contained virtualenv. Nothing
# from this stage except /app/.venv is copied forward, so uv itself, pip's
# and uv's download caches, and any compiler toolchain a wheel build might
# need never reach the image that actually runs.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Bytecode is compiled once here (slower build, faster cold start), and
# link-mode=copy makes the venv a real copy rather than hardlinks into a
# cache directory that won't exist in the runtime stage.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir uv==0.12.20

WORKDIR /app

# Only the dependency manifests are copied before the install, so this
# layer (the slow one) is rebuilt only when pyproject.toml or uv.lock
# change - editing application code below never invalidates it.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev


# ---------------------------------------------------------------------------
# Stage 2 - runtime: the same slim base (so the venv's interpreter symlink
# resolves identically) plus the finished venv and the application code.
# No uv, no dev dependencies (pytest, pytest-asyncio, respx), no build caches.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# A fixed, high, non-login UID/GID: the process never runs as root, and a
# Kubernetes runAsNonRoot check can verify a numeric UID (it can't verify a
# user *name*).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
# WORKDIR creates /app owned by root; the app user needs to be able to create
# files directly inside it (COPY --chown below only chowns what it copies).
RUN chown app:app /app

# The venv stays root-owned: the running process can read and execute it but
# can't modify installed packages.
COPY --from=builder /app/.venv /app/.venv

# Application code changes most often, so it comes last. It is owned by the
# app user because Chainlit writes its own runtime files (.files/, chainlit
# config) into the working directory.
COPY --chown=app:app . .

USER app

EXPOSE $PORT

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
