$ErrorActionPreference = "Stop"
$env:COMPOSE_BAKE = "false"
docker compose up --build -d
docker compose exec auth-service python /app/scripts/smoke-test.py
