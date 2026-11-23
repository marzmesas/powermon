FROM python:3.12-slim

# Standard library only: no pip install step.
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# pdftotext, for reading a contract PDF's text from the Settings page.
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

COPY powermon.py dashboard.html pwr config.example.toml docker-entrypoint.sh /app/

# Config, database and anything else written at runtime lives here.
VOLUME /data

EXPOSE 8787

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
