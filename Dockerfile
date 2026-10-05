FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && useradd --create-home relay && mkdir /data && chown relay /data
USER relay

ENTRYPOINT ["planning-relay"]
CMD ["sync"]
