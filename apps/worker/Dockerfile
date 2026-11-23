FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY worker.py .
USER 10001
ENTRYPOINT ["celery", "-A", "worker:app", "worker", "--concurrency=1", "--loglevel=INFO"]
