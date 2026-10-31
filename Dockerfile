# ==============================================================
# Stage 1: Build the React + Three.js frontend
# ==============================================================
FROM node:20-alpine AS frontend-builder
WORKDIR /build

# Install dependencies
COPY nexus/frontend/package*.json ./
RUN npm ci

# Copy frontend source and build production bundle
COPY nexus/frontend/ ./
RUN npm run build

# ==============================================================
# Stage 2: Python FastAPI backend + built frontend
# ==============================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXUS_DEMO_FILL=1 \
    NEXUS_AUTO_FAULTS=1 \
    NEXUS_OPEN_ACCESS=1 \
    NEXUS_OPEN_LOCAL=1

WORKDIR /app

# Install system dependencies if any
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY nexus/backend/requirements.txt ./nexus/backend/requirements.txt
RUN pip install --no-cache-dir -r ./nexus/backend/requirements.txt

# Copy predictive maintenance module (dependency of backend analysis)
COPY predictive_maintenance/ ./predictive_maintenance/

# Copy backend source
COPY nexus/backend/ ./nexus/backend/

# Copy compiled frontend from Stage 1 into nexus/frontend/dist
COPY --from=frontend-builder /build/dist ./nexus/frontend/dist

# Expose port (Render sets $PORT dynamically)
EXPOSE 8000
ENV PORT=8000

WORKDIR /app/nexus/backend

# Run Uvicorn server bound to 0.0.0.0 and dynamic $PORT
CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
