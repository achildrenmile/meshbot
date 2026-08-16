# --- Build ---------------------------------------------------------------
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Laufzeit ------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY --from=builder /install /usr/local
COPY meshbot ./meshbot
COPY data ./data

# Nicht als root. Der Bot braucht kein Schreibrecht im Dateisystem.
# /data gehoert dem Dienstbenutzer, sonst kann er im gemounteten Volume nicht
# schreiben — ein frisches Named Volume uebernimmt die Rechte aus dem Image.
RUN useradd --system --uid 10001 meshbot \
    && mkdir -p /data \
    && chown -R meshbot:meshbot /srv /data
USER meshbot
EXPOSE 8080

# Prueft absichtlich nur den eigenen Prozess und die MQTT-Verbindung.
# Eine Stoerung bei GeoSphere ist kein Grund fuer einen Neustart.
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3).status==200 else 1)"

CMD ["python", "-m", "meshbot.main"]
