FROM python:3.11-slim

WORKDIR /app

# Install system deps for ChromaDB + sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy monorepo
COPY . .

# Install with RAG extras (chromadb + sentence-transformers)
RUN pip install --no-cache-dir -e ".[rag]"

# Create non-root user for security (least-privilege)
RUN useradd -r -m -d /home/kazma -s /bin/bash kazma \
    && mkdir -p /app/kazma-data /home/kazma/.kazma/vector_memory \
    && chown -R kazma:kazma /app /home/kazma

USER kazma

EXPOSE 8000

# --host 0.0.0.0 is required inside Docker so the port mapping
# (ports: 8000:8000) actually reaches the service. Docker's network
# isolation provides the security boundary; 127.0.0.1 inside a
# container means only the container itself can reach the port.
CMD ["python", "-m", "uvicorn", "kazma_ui.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "15"]
