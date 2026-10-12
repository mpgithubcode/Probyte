# Document converter for file previews: Word, PowerPoint, OpenDocument… -> PDF with LibreOffice (no GUI).
# Internal only (no published port): Probyte's app and worker send it a file and get a PDF back.
FROM debian:trixie-slim
ARG VERSION=dev
LABEL org.opencontainers.image.title="Probyte office converter" \
      org.opencontainers.image.description="Probyte's document converter for file previews (LibreOffice without a GUI)" \
      org.opencontainers.image.source="https://github.com/mpgithubcode/Probyte" \
      org.opencontainers.image.url="https://github.com/mpgithubcode/Probyte" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${VERSION}"
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-impress-nogui libreoffice-calc-nogui \
      fonts-dejavu fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea fonts-noto-core python3 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m office
COPY server.py /server.py
USER office
EXPOSE 3000
CMD ["python3", "/server.py"]
