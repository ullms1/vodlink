FROM node:20-alpine AS frontend-build
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN echo "deb http://deb.debian.org/debian bookworm main contrib" \
       > /etc/apt/sources.list.d/debian-bookworm.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    && curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key \
       | gpg --dearmor -o /etc/apt/trusted.gpg.d/jellyfin.gpg \
    && echo "deb [arch=amd64] https://repo.jellyfin.org/debian bookworm main" \
       > /etc/apt/sources.list.d/jellyfin.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends jellyfin-ffmpeg7 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg \
    && ln -sf /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=frontend-build /build/dist ./static
RUN mkdir -p /app/data
ARG VERSION=dev
ENV APP_VERSION=${VERSION}
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
