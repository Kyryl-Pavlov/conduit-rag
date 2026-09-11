# Shared image for the local dispatcher/worker containers. Build context is
# the repo root (see docker-compose.yml's `build.context: ../..`) so this can
# reach both the root requirements.txt and src/.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src
COPY infra/local/scripts/ ./scripts
COPY infra/local/aws-config ./aws-config

ENV PYTHONPATH=/app/src
ENV AWS_CONFIG_FILE=/app/aws-config

ENTRYPOINT ["python"]
