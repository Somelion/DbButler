from pydantic_settings import BaseSettings

INSECURE_DEFAULT_SECRET_KEY = "dev-only-insecure-key-change-me"


class Settings(BaseSettings):
    """Backend configuration, sourced from environment variables set in docker-compose.yml."""

    store_database_url: str = "postgresql://pgdba:pgdba@pgdba-store:5432/pgdba_store"

    # Encrypts stored target credentials (and AI provider keys, webhook
    # URLs) at rest. The insecure default below is only ever accepted if
    # allow_insecure_secret_key is also set — see the check below.
    secret_key: str = INSECURE_DEFAULT_SECRET_KEY

    # Explicit, named escape hatch for a genuinely disposable local
    # instance (e.g. a one-off look at the demo data) — anything else
    # should set a real secret_key instead of flipping this.
    allow_insecure_secret_key: bool = False

    # Required for the API to serve any /api/* request — see app/auth.py.
    # Empty by default so a misconfigured deployment fails loudly (503)
    # instead of silently serving every request unauthenticated.
    auth_token: str = ""

    # Comma-separated list of origins the UI is allowed to call the API from.
    cors_allowed_origins: str = "http://localhost:5173"


settings = Settings()

if settings.secret_key == INSECURE_DEFAULT_SECRET_KEY and not settings.allow_insecure_secret_key:
    raise RuntimeError(
        "SECRET_KEY is still the well-known insecure default. Every stored credential (target "
        "passwords, AI provider keys, webhook URLs) is encrypted with this key, so leaving it "
        "unchanged means anyone who knows the default can decrypt them. Set APP_SECRET_KEY in "
        ".env to a random value, e.g.:\n"
        "    openssl rand -hex 32\n"
        "For a genuinely disposable local instance only, set APP_ALLOW_INSECURE_SECRET_KEY=true "
        "in .env to bypass this check."
    )
