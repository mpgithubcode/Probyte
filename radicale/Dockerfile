FROM tomsquest/docker-radicale:3.5.10.0
COPY config /config/config
COPY start.sh /usr/local/bin/probyte-start.sh
RUN chmod +x /usr/local/bin/probyte-start.sh
ENTRYPOINT ["probyte-start.sh"]
CMD ["/venv/bin/radicale", "--config", "/config/config"]
