# Built from the repo root (docker-compose.yml: context ".") so the
# repo-root .version file can be copied in — see app/version.py.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Unprivileged runtime user — nothing the backend does needs root.
RUN groupadd --system --gid 10001 dbbutler \
    && useradd --system --uid 10001 --gid dbbutler --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin dbbutler

WORKDIR /app

# requirements.txt is a hash-locked file generated from requirements.in;
# --require-hashes refuses anything that isn't pinned to a known artifact.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY backend/app ./app
COPY .version ./.version

USER dbbutler

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
