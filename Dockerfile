FROM python:3.11-slim

WORKDIR /app

# Install system deps for ChromaDB + sentence-transformers + healthchecks
# git is required by the git_* tools (git_status/push/commit/...) — without it
# every git tool fails with FileNotFoundError inside the container. (Audit D1.)
#
# Document Intelligence system deps: without a font, ReportLab falls back to
# Helvetica (zero Arabic glyphs); without LibreOffice, the good DOCX->PDF
# shaping route is never attempted. Together that meant every Arabic PDF this
# image produced came out blank behind a warning string nobody reads.
# Tesseract + the ara traineddata restores the OCR recovery tier; ClamAV
# restores the quarantine scan.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    libpq5 \
    fonts-noto-naskh-arabic \
    fonts-noto-core \
    fonts-dejavu-core \
    libreoffice-writer \
    libreoffice-calc \
    tesseract-ocr \
    tesseract-ocr-ara \
    tesseract-ocr-eng \
    clamav \
    clamav-freshclam \
    && rm -rf /var/lib/apt/lists/*

# Fetch ClamAV signatures at BUILD time.
#
# The `clamav` package ships the scanner and no virus database. Without one,
# `clamscan` exits with an error — and `scan_if_configured` treats an error as
# "skipped" under the default (`malware_scan=auto`, `fail_closed=false`), so
# every upload passed a scan that never ran, behind an INFO log (audit
# 2026-09-16 F-8). The image advertised malware scanning and did not do it.
#
# `|| true`: freshclam needs network, and a mirror hiccup must not fail the
# build. The entrypoint refreshes signatures at runtime anyway, and
# `/health/details` reports `malware.available` so the gap is visible rather
# than silent.
RUN freshclam --quiet || true

# Copy monorepo
COPY . .

# Install with RAG + Postgres extras (multi-replica ready)
# document-platform brings pymupdf + pypdfium2, without which convert and
# redact return 503 in a container that otherwise reports healthy.
RUN pip install --no-cache-dir -e ".[rag,postgres,document-platform]"

# Create non-root user for security (least-privilege)
RUN useradd -r -m -d /home/kazma -s /bin/bash kazma \
    && mkdir -p /app/kazma-data /home/kazma/.kazma/vector_memory \
    && chown -R kazma:kazma /app /home/kazma \
    && chmod +x /app/scripts/docker-entrypoint.sh

USER kazma

EXPOSE 8000

# --host 0.0.0.0 is required inside Docker so the port mapping
# (ports: 8000:8000) actually reaches the service. Docker's network
# isolation provides the security boundary; 127.0.0.1 inside a
# container means only the container itself can reach the port.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "kazma_ui.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "15", "--ws-ping-interval", "20", "--ws-ping-timeout", "20"]
