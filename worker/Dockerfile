# Thin wrapper: the worker entrypoint lives at backend/app/worker.py so the run
# command is identical in Docker and in local development.
FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
ENV PYTHONPATH=/app
RUN useradd -m -u 1000 app && mkdir -p /app/data/uploads && chown -R app /app
USER app
CMD ["arq", "app.worker.WorkerSettings"]
