# Fargate task image for real (non-fake) video analysis. Unlike Lambda, ECS
# has no Layer mechanism -- common/ is copied directly into the image at
# build time instead, mirroring infra/local/python.Dockerfile's
# copy-src+PYTHONPATH pattern. Build context is the repo root.
#
# Image build/push to ECR is out of scope for this pass (no CI exists yet,
# and terraform apply hasn't run) -- this Dockerfile is authored, not built.
FROM python:3.12-slim

WORKDIR /app

COPY src/video_task/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/common ./src/common
COPY src/video_task ./src/video_task

ENV PYTHONPATH=/app/src

ENTRYPOINT ["python", "-m", "video_task.main"]
