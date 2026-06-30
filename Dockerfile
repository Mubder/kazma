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

# Create data dirs for SQLite + ChromaDB
RUN mkdir -p /app/kazma-data /root/.kazma/vector_memory

EXPOSE 8000

# --graceful-timeout 15 gives uvicorn time to drain on SIGTERM
CMD ["python", "-m", "uvicorn", "kazma_ui.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000", "--timeout-graceful-shutdown", "15"]
