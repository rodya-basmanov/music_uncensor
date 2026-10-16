FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg — для конвертации в MP3 и обработки обложек
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

# healthcheck по файлу heartbeat (пишется ботом раз в 30с)
HEALTHCHECK --interval=60s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import os,time,sys; p=os.path.join(os.environ.get('DATA_DIR','./data'),'.heartbeat'); sys.exit(0 if (os.path.exists(p) and time.time()-os.path.getmtime(p)<200) else 1)"

CMD ["python", "bot/main.py"]
