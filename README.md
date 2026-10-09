# pafare

Hotel room booking: a FastAPI backend (`backend/`) with a PostgreSQL database. The `frontend/` folder is empty for now.

## Run everything

```bash
cp .env.example .env        # optional: every value has a development default
docker compose up --build   # starts PostgreSQL, applies the migrations, then starts the API
```

The API is at http://localhost:8000 (interactive docs at `/docs`, health at `/v1/health/`).

Create the first administrator, who can then manage everyone else through the API:

```bash
docker compose run --rm api python -m scripts.create_superadmin you@example.com 'A-strong-Password1' First Last
```

`docker compose down` stops it and keeps the data; `docker compose down -v` also deletes the database.
In production set `ENVIRONMENT=production` and a long random `SECRET_KEY` (it refuses to start with the default).

More detail on the backend is in [backend/README.md](backend/README.md).
