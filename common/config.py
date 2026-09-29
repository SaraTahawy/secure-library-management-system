import os


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def database_url() -> str:
    return (
        f"postgresql://{env('DB_USER')}:{env('DB_PASSWORD')}"
        f"@postgres:5432/{env('POSTGRES_DB')}"
    )
