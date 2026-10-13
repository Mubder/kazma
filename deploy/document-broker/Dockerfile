# Kazma document worker broker image (KAZMA_DOCUMENT_WORKER_ISOLATION=container).
#
# The ONLY container that holds the Docker engine socket for document work.
# It runs kazma-core/kazma_core/documents/worker_broker.py (standard library
# only) and the docker CLI, nothing else: it accepts authenticated "run
# worker X" requests on a unix socket shared with Kazma and starts each job
# in a fresh container of the worker image under one fixed, attested policy.
# Kazma itself never mounts /var/run/docker.sock.
#
# Build from the repository root:
#   docker build -f deploy/document-broker/Dockerfile -t kazma-document-broker:local .
FROM docker:29.0-cli@sha256:858bb1e05af16840f5a55143c3e5e14073891fbc92f2c1f6f38dd9c5f2cca03c AS cli

FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
COPY --from=cli /usr/local/bin/docker /usr/local/bin/docker
COPY kazma-core/kazma_core/documents/worker_broker.py /opt/kazma-broker/worker_broker.py
RUN python -m py_compile /opt/kazma-broker/worker_broker.py \
    && docker --version

# The docker CLI keeps its config under HOME; the root filesystem is mounted
# read-only in deploy/docker-compose.document-broker.yml, /tmp is a tmpfs.
# The broker names its CLI here: it is this one file, without Kazma's lookup
# module beside it (worker_broker._find_docker_cli).
ENV HOME=/tmp
ENV KAZMA_DOCKER_BIN=/usr/local/bin/docker
WORKDIR /tmp
ENTRYPOINT ["python", "-I", "/opt/kazma-broker/worker_broker.py"]
