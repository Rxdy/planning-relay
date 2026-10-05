FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && useradd --create-home relay && mkdir /data && chown relay /data
USER relay

ENV TZ=Europe/Paris
HEALTHCHECK --interval=5m --timeout=10s --start-period=1m CMD ["planning-relay", "sante"]
ENTRYPOINT ["planning-relay"]
CMD ["service"]
