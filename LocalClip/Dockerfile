FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . ./

ENV HOST=0.0.0.0 \
    PORT=10000 \
    LOCALCLIP_DATA_DIR=/var/data

EXPOSE 10000
CMD ["python", "app.py"]
