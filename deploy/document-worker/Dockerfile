# Kazma document worker image (KAZMA_DOCUMENT_WORKER_ISOLATION=container).
#
# One untrusted document job per container. Only the document worker broker
# (kazma-core/kazma_core/documents/worker_broker.py) starts it, and always
# with the same policy: no network, no mounts, read-only root, a size-bounded
# tmpfs /tmp, memory/swap/CPU/PID/file limits, every capability dropped,
# no-new-privileges, uid 10001 and Docker's default seccomp profile. The
# broker attests that policy from inside this image before it serves.
#
# The image holds no credential and no Kazma state: the kazma_core source,
# the parser/renderer engines and their system binaries. It never needs the
# Docker socket.
#
# Build from the repository root:
#   docker build -f deploy/document-worker/Dockerfile -t kazma-document-worker:local .

# ── The neural OCR engine's models (documents.ocr.engine) ──────────────────
# Downloaded from their pinned sources (size and SHA-256 checked), EasyOCR's
# two PyTorch recognizers converted to ONNX and checked against PyTorch, all
# by scripts/ocr_models.py. Only the resulting /models folder reaches the
# final image: no PyTorch, no network at run time (the worker has none).
FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 AS ocr-models
COPY deploy/document-worker/ocr-models.txt /tmp/ocr-models.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/ocr-models.txt
COPY kazma-core/kazma_core/documents/ocr/neural_models.py /src/kazma-core/kazma_core/documents/ocr/neural_models.py
COPY scripts/ocr_models.py /src/scripts/ocr_models.py
RUN python -I /src/scripts/ocr_models.py install --dir /models

FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

# Tesseract (+ara/eng) for OCR, poppler-utils for page rasterization,
# LibreOffice (no GUI) for legacy Office conversion and the libreoffice
# renderer, Pango for WeasyPrint, and the Noto/DejaVu faces Arabic output
# needs. The same system set as the application image, without the server.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-ara \
        tesseract-ocr-eng \
        poppler-utils \
        libreoffice-writer-nogui \
        libreoffice-calc-nogui \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        fonts-noto-core \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/document-worker/requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/worker-requirements.txt \
    && rm /tmp/worker-requirements.txt

# Writable locations exist only in the job's tmpfs; the entry point creates
# them. kazma_core's settings and workspace stores compute a default database
# path when imported, which creates the data directory: KAZMA_DATA_DIR keeps
# that inside the tmpfs (the attestation refuses a /kazma-data in the image).
# One thread per math library keeps import-time buffers inside the memory
# limit.
ENV HOME=/tmp/home \
    XDG_CACHE_HOME=/tmp/cache \
    XDG_CONFIG_HOME=/tmp/config \
    KAZMA_DATA_DIR=/tmp/kazma-data \
    KAZMA_OCR_MODEL_DIR=/opt/kazma/ocr-models \
    OMP_NUM_THREADS=1 \
    OMP_THREAD_LIMIT=1 \
    OPENBLAS_NUM_THREADS=1

# The neural OCR models, root-owned and read-only like the code (the
# capability report names the engine ready only when they verify).
COPY --from=ocr-models /models /opt/kazma/ocr-models

# kazma_core is importable through a .pth file (python -I ignores PYTHONPATH
# but still reads site-packages). Root owns it; the worker cannot change it,
# and the read-only root filesystem would refuse anyway.
COPY kazma-core/kazma_core /opt/kazma/kazma_core
RUN find /opt/kazma -name __pycache__ -prune -exec rm -rf {} + \
    && python -c "import site; open(site.getsitepackages()[0] + '/kazma-worker.pth', 'w').write('/opt/kazma\n')" \
    && python -m compileall -q /opt/kazma \
    && python -I -c "import kazma_core.documents.worker_entry, kazma_core.documents.parser_worker, kazma_core.documents.mutation_worker, kazma_core.documents.renderer_worker" \
    && python -I -c "from kazma_core.documents.ocr.neural_models import verify_model_directory as v; s = v('/opt/kazma/ocr-models'); assert s.ready, s.reason" \
    && rm -rf /tmp/kazma-data \
    && test ! -e /kazma-data

USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "-m", "kazma_core.documents.worker_entry"]
