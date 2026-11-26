# What CI's Linux test jobs have, for the checks before a push
# (scripts/prepush.py, docs/agents/testing-2-full-suite.md).
#
# Python 3.11 (ci.yml's PYTHON_VERSION) with the Tests job's locked set
# (requirements.txt beside this file in the build context: `uv export` of the
# `test` extra and the `ci-light` and `build` groups, hash-checked; no
# psycopg), the system programs that job installs, git and node as the
# GitHub runner has them, and an account that is not root, as there. No
# Kazma source: each run copies the tree in. Built once per uv.lock; the tag
# is a hash of this file and the exported set.
FROM python:3.11-slim@sha256:61e01f72680f86a5279ad510818ab7b2ee9f049822589d4e34e33e044360356c

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git nodejs procps ca-certificates \
        libreoffice-writer-nogui fonts-noto-core fontconfig \
        tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && fc-list | grep -qi naskh

RUN useradd --create-home --uid 1001 runner && mkdir /work && chown runner /work
USER runner
WORKDIR /work
COPY requirements.txt /tmp/requirements.txt
# An environment of that account's own: each run installs Kazma into it.
RUN python -m venv /home/runner/venv \
    && /home/runner/venv/bin/pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt
ENV PATH=/home/runner/venv/bin:$PATH PYTHONUNBUFFERED=1 KAZMA_DB_BACKEND=sqlite
