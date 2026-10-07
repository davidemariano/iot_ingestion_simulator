# Simulatore IoT: API /iot, broker emulato, ingestion e task periodici (iotsim serve).
# La stessa immagine esegue anche la modalità "traffico" verso un Mosquitto reale.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# 1) solo le dipendenze, alle versioni verificate: cambiare il codice non le reinstalla
COPY pyproject.toml constraints.txt ./
RUN mkdir -p src/iotsim && touch src/iotsim/__init__.py \
    && pip install -c constraints.txt ".[mqtt]" \
    && pip uninstall -y iotsim

# 2) il pacchetto
COPY src ./src
RUN pip install --no-deps . && rm -rf /app/src /app/build

RUN useradd --system --uid 10001 --home-dir /app iotsim
USER iotsim

ENV IOTSIM_HOST=0.0.0.0 \
    IOTSIM_PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"]

CMD ["iotsim", "serve"]
