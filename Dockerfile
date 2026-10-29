FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Браузер и системные зависимости для узла проверки.
RUN playwright install --with-deps chromium

COPY . .

ENV SPK_STORE_PATH=/data/spk.db \
    SPK_PROFILE_FILE=/data/profiles.json

VOLUME ["/data", "/app/fixtures"]

CMD ["python", "-m", "worker.main"]
