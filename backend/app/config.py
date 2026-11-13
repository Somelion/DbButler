from pydantic_settings import BaseSettings

INSECURE_DEFAULT_SECRET_KEY = "dev-only-insecure-key-change-me"

# example.env used to ship this exact value as APP_AUTH_TOKEN, so anyone who
# copied it unchanged had an API "protected" by a publicly known token.
KNOWN_INSECURE_AUTH_TOKENS = {INSECURE_DEFAULT_SECRET_KEY, "changeme", "change-me", "secret", "token"}
# `openssl rand -hex 32` (what README/example.env suggest) gives 64 chars;
# anything under 32 is short enough to be guessable or hand-typed.
MIN_AUTH_TOKEN_LENGTH = 32


class Settings(BaseSettings):
    """Backend configuration, sourced from environment variables set in docker-compose.yml."""

    store_database_url: str = "postgresql://pgdba:pgdba@pgdba-store:5432/pgdba_store"

    # Store connection pool (app/db/store.py). Request handlers and the
    # scheduler's per-target workers share it; a caller waits up to the
    # timeout for a free connection instead of opening an unbounded number.
    store_pool_max_size: int = 20
    store_pool_timeout_seconds: float = 30.0

    # How many targets each scheduler job works on at the same time
    # (app/scheduler.py), so one slow or unreachable target no longer holds
    # up every other target's collection.
    scheduler_max_parallel_targets: int = 4

    # Refactoring Advisor's partitioning rule (app/refactoring_advisor.py):
    # a table smaller than this is never suggested for partitioning — below
    # roughly a gigabyte, partition management usually costs more than it
    # saves. Lower it to try the rule on the small demo tables.
    refactor_partition_min_table_mb: int = 1024

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

    # Separate, narrower tokens for the machine integrations (app/auth.py):
    # MCP_TOKEN opens only the read-only MCP endpoint (/api/mcp) an AI agent
    # talks to, METRICS_TOKEN only the Prometheus scrape endpoint. Neither
    # can reach the rest of the API (Apply DDL, settings, ...). Unset = that
    # endpoint is off (404).
    mcp_token: str = ""
    metrics_token: str = ""

    # The address people open DbButler at (e.g. https://dbbutler.example.com),
    # used for links in the weekly report's webhook message. Optional: the
    # message simply has no link without it.
    public_url: str = ""

    # Feature flags (FEATURE_* in docker-compose.yml, APP_FEATURE_* in .env),
    # read by the UI from /api/features. Off by default.
    #   feature_workload_3d: the Workload Map's "3D map" view.
    feature_workload_3d: bool = False

    # Comma-separated list of origins the UI is allowed to call the API from.
    cors_allowed_origins: str = "http://localhost:5173"

    # When true, the backend also writes its log to {log_dir}/backend.log
    # (rotating). Controlled by LOG_TO_FILE in .env.
    log_to_file: bool = False
    log_dir: str = "/logs"


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

# An empty token is left to app/auth.py, which fails closed (503 on every
# request) — but a token that's set to a known or trivially short value
# would silently "work", so it's refused at startup instead.
if settings.auth_token and (
    settings.auth_token in KNOWN_INSECURE_AUTH_TOKENS or len(settings.auth_token) < MIN_AUTH_TOKEN_LENGTH
):
    raise RuntimeError(
        f"AUTH_TOKEN is a well-known default or shorter than {MIN_AUTH_TOKEN_LENGTH} characters. It's the "
        "only thing protecting the API, so it must be a long random value. Set APP_AUTH_TOKEN in .env, "
        "e.g.:\n"
        "    openssl rand -hex 32"
    )

# The integration tokens get the same rule, and must not repeat AUTH_TOKEN:
# the point of a separate token is that leaking it (it sits in an AI agent's
# or Prometheus' config) can't unlock the rest of the API.
for _name, _token in (("MCP_TOKEN", settings.mcp_token), ("METRICS_TOKEN", settings.metrics_token)):
    if not _token:
        continue
    if _token in KNOWN_INSECURE_AUTH_TOKENS or len(_token) < MIN_AUTH_TOKEN_LENGTH:
        raise RuntimeError(
            f"{_name} is a well-known default or shorter than {MIN_AUTH_TOKEN_LENGTH} characters. Set "
            f"APP_{_name} in .env to a long random value (openssl rand -hex 32), or leave it empty to "
            "keep that endpoint off."
        )
    if _token == settings.auth_token:
        raise RuntimeError(
            f"{_name} is the same as AUTH_TOKEN. It must be a different value, so whoever holds it "
            "(an AI agent, a Prometheus server) can't use the rest of the API."
        )
if settings.mcp_token and settings.mcp_token == settings.metrics_token:
    raise RuntimeError(
        "MCP_TOKEN and METRICS_TOKEN are the same value. Give each its own, so a Prometheus "
        "server's token can't also query the MCP endpoint."
    )
