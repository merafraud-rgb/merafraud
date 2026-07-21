# MeraFraud API — production container
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY model/ ./model/
COPY data/generate_data.py ./data/generate_data.py
COPY api/ ./api/

# tenants.json kalıcı olması için bir volume'a bağlanmalı (bkz. docker-compose.yml)
RUN mkdir -p /app/data

EXPOSE 5000

# debug modunda değil, gunicorn ile üretim sunucusu
CMD ["gunicorn", "--chdir", "api", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "30", "app:app"]
