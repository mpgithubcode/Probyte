FROM tomsquest/docker-radicale:3.5.10.0
ARG VERSION=dev
LABEL org.opencontainers.image.title="Probyte Radicale" \
      org.opencontainers.image.description="Probyte's bundled CalDAV/CardDAV server (Radicale, set up for Probyte)" \
      org.opencontainers.image.source="https://github.com/mpgithubcode/Probyte" \
      org.opencontainers.image.url="https://github.com/mpgithubcode/Probyte" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.version="${VERSION}"
COPY config /config/config
COPY start.sh /usr/local/bin/probyte-start.sh
RUN chmod +x /usr/local/bin/probyte-start.sh
ENTRYPOINT ["probyte-start.sh"]
CMD ["/venv/bin/radicale", "--config", "/config/config"]
