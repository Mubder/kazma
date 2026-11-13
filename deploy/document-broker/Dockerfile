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
FROM docker:29.8-cli@sha256:35cfcf893c20f2931f4909151b5407aaaeab711277de7ce8b8f521f095d54c32 AS cli

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
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
