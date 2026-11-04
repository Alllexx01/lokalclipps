FROM denoland/deno:bin-2.9.8 AS deno

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# YouTube requires a supported JavaScript runtime for yt-dlp's EJS challenge
# solver.  The slim Python image does not contain one by default.
COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . ./

ENV HOST=0.0.0.0 \
    PORT=10000 \
    LOCALCLIP_DATA_DIR=/var/data

EXPOSE 10000
CMD ["python", "app.py"]
