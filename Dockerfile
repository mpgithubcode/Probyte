FROM node:20-alpine AS frontend
WORKDIR /src
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
# pg_dump / pg_restore for backups (a newer client works with the Postgres 16 server); pdftoppm and fonts for file previews
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client poppler-utils fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /src/dist ./static
EXPOSE 8000
# --proxy-headers: trust X-Forwarded-Proto from the reverse proxy (for Secure cookies)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
