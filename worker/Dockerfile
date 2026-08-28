FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY worker/main.py ./worker_main.py
ENV PYTHONPATH=/app
CMD ["arq", "worker_main.WorkerSettings"]
