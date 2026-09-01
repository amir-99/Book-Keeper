# The one place this repository builds an image instead of pinning a published
# digest in compose.yml. The base image digest is pinned here so the build
# stays reproducible.
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      pandoc fonts-dejavu fonts-liberation curl \
 && rm -rf /var/lib/apt/lists/*

# House styling for .docx and .odt renders. Regenerating the default here keeps
# a binary out of version control; replace /opt/reference.docx with a Word-
# restyled copy (mount it or extend this image) to change fonts and headings
# without writing OOXML.
RUN pandoc --print-default-data-file reference.docx > /opt/reference.docx

COPY app.py /opt/app.py

EXPOSE 8090
CMD ["python", "/opt/app.py"]
