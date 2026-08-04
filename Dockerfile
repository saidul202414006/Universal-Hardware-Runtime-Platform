# Build Dashboard
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY apps/dashboard/package*.json ./
RUN npm ci
COPY apps/dashboard/ ./
RUN npm run build

# Build Runtime
FROM python:3.11-slim

# Install system dependencies (tools required by adapters)
RUN apt-get update && apt-get install -y \
    curl \
    udev \
    openocd \
    avrdude \
    && rm -rf /var/lib/apt/lists/*

# Install arduino-cli
RUN curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh

# Set up Python environment with uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY packages/ ./packages/
COPY adapters/ ./adapters/
COPY plugins/ ./plugins/
COPY runtimes/ ./runtimes/
COPY configs/ ./configs/

RUN uv venv .venv
RUN uv pip install -e ".[postgres]"

# Copy built dashboard
COPY --from=frontend-builder /app/out ./apps/dashboard/out

# Expose HTTP port
EXPOSE 8765

# Start the runtime server
CMD [".venv/bin/uhr-server"]
