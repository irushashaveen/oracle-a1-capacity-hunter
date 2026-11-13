FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CONFIG_PATH=/app/config.json \
    HOME=/home/app

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --create-home --gid app --shell /usr/sbin/nologin app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app app ./app

USER app:app
EXPOSE 8080

ENTRYPOINT ["python", "-m", "app.main"]
CMD ["--web"]
