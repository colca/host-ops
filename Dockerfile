FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST_OPS_CONFIG_PATH=/secrets/property.json

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[cloud]" \
    && useradd --create-home --uid 10001 hostops

USER hostops
ENTRYPOINT ["python", "-m", "host_ops.cli"]
CMD ["cloud-cycle"]
