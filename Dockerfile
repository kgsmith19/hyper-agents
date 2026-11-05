FROM python:3.12-slim

WORKDIR /app
RUN useradd --create-home --uid 10001 hyperagents
COPY pyproject.toml uv.lock README.md ./
RUN pip install --no-cache-dir uv==0.11.32 && uv sync --frozen --no-dev
COPY src ./src
USER hyperagents
CMD ["uv", "run", "hyper-agents", "doctor", "--json"]
