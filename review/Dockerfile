# Keep the only repository-reading runtime deliberately small. Repository
# content is data: this image has no compiler, package manager, or project
# runtime with which to execute it.
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY app.py /opt/app.py

EXPOSE 8091
CMD ["python", "/opt/app.py"]

