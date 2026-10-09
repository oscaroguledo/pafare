# pafare

Hotel room booking API (FastAPI, PostgreSQL).

## Run with Docker

Build from this folder and pass configuration as environment variables (`.env*` files are not copied into the image):

```bash
docker build -t pafare-api .

docker run --rm -p 8000:8000 \
  -e ENVIRONMENT=production \
  -e DATABASE_URL=postgresql://user:password@host:5432/pafare \
  -e SECRET_KEY=change-me-to-a-long-random-string \
  pafare-api
```

The API is on http://localhost:8000 (docs at `/docs`, health at `/v1/health/`). The container listens on `$PORT` (default 8000).

## First-time setup

Create the tables and the seeded roles and permissions, then the first administrator:

```bash
docker run --rm -e DATABASE_URL=... pafare-api alembic upgrade head
docker run --rm -e DATABASE_URL=... -e ENVIRONMENT=production pafare-api \
  python -m scripts.create_superadmin you@example.com 'A-strong-Password1' First Last
```

## Without Docker

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --reload
```
