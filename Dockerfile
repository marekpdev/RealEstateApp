# Step 1: Use a slim, stable official Python image
FROM python:3.12-slim

# Step 2: Install system dependencies needed to fetch external installers
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Step 3: Install 'uv' natively inside the system environment layers
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:${PATH}"

# Step 4: Establish our working path context inside the container image
WORKDIR /app

# NEW: Explicitly activate the uv virtual environment system-wide inside the image
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# Step 5: Copy configuration dependencies first to leverage caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# Step 6: Copy your actual code files into the container directory
COPY . .

# Step 7: Expose our target port variables out to the system environment
ENV PORT=8080
EXPOSE 8080

# Step 8: Fire up the non-blocking production server runtime
# We read the environmental $PORT variable dynamically so Azure can control it.
# -h = Headless mode (prevents trying to auto-launch an interface window inside Linux)
CMD ["sh", "-c", "uv run chainlit run app.py -h --host 0.0.0.0 --port $PORT"]