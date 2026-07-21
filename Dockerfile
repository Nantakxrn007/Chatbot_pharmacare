# ── Stage 1: build the React frontend ──────────────────────────────────────
FROM node:20-slim AS frontend-build

WORKDIR /app/frontend/react-app

COPY frontend/react-app/package.json frontend/react-app/package-lock.json ./
RUN npm ci

COPY frontend/react-app/ ./
RUN npm run build

# ── Stage 2: python backend + built frontend ────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project (runtime may override backend/rag via compose mounts)
COPY backend/ ./backend/
COPY rag/ ./rag/
COPY pipeline.py .

# Built React app from stage 1 — no Node/npm needed at runtime
COPY --from=frontend-build /app/frontend/react-app/dist ./frontend/react-app/dist

# Expose
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Run server
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
