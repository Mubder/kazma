FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy monorepo
COPY . .

# Install core + gateway + UI (without heavy RAG deps)
RUN pip install --no-cache-dir -e .

# Create data dirs
RUN mkdir -p /app/kazma-data /root/.kazma/vector_memory

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "kazma_ui.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
