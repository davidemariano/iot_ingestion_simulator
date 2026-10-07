# Immagine unica: dashboard Angular compilata + simulatore Python che la serve.

# --- dashboard ------------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY dashboard/package.json dashboard/package-lock.json dashboard/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npx ng build

# --- simulatore -----------------------------------------------------------------
FROM python:3.13-slim
WORKDIR /app
COPY simulator/pyproject.toml ./
COPY simulator/src ./src
RUN pip install --no-cache-dir ".[mqtt]"
COPY --from=web /web/dist/dashboard/browser /app/static
ENV IOTSIM_HOST=0.0.0.0 \
    IOTSIM_STATIC_DIR=/app/static
EXPOSE 8000
CMD ["iotsim", "serve"]
