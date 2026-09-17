#!/usr/bin/env bash
# Pre-flight check for PostgreDba's docker-compose setup.
# Checks for a .env file (optional — see README.md) and whether the ports
# docker-compose.yml wants to bind are actually free, suggesting a nearby
# free port for anything that's busy.
#
# This script only reports; it never modifies anything or starts containers.
# Not run automatically — run it yourself with: bash pre-flight.sh

set -u

# Known .env override keys (see docker-compose.yml). Presence is reported,
# values are never printed — some of these are credentials.
ENV_KEYS="STORE_POSTGRES_USER STORE_POSTGRES_PASSWORD STORE_POSTGRES_DB APP_SECRET_KEY APP_ALLOW_INSECURE_SECRET_KEY APP_AUTH_TOKEN DEMO_POSTGRES_USER DEMO_POSTGRES_PASSWORD DEMO_POSTGRES_DB UI_PORT BACKEND_PORT DEMO_PORT"

INSECURE_DEFAULT_SECRET_KEY="dev-only-insecure-key-change-me"

# port|env var to override it|label
PORT_CHECKS="5173|UI_PORT|UI (pgdba-ui)
8000|BACKEND_PORT|Backend API (pgdba-backend)
5433|DEMO_PORT|Demo target (pgdba-demo-target, only needed with --profile demo)"

is_port_free() {
  local port="$1"
  if command -v timeout >/dev/null 2>&1; then
    ! timeout 1 bash -c "echo > /dev/tcp/127.0.0.1/${port}" 2>/dev/null
  else
    ! bash -c "echo > /dev/tcp/127.0.0.1/${port}" 2>/dev/null
  fi
}

suggest_free_port() {
  local base="$1"
  local candidate offset
  for offset in $(seq 1 20); do
    candidate=$((base + offset))
    if is_port_free "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done
  echo ""
}

echo "== .env file =="
if [ -f .env ]; then
  echo "Found .env — docker compose loads it automatically."
  found_any=0
  for key in $ENV_KEYS; do
    if grep -qE "^${key}=" .env 2>/dev/null; then
      echo "  - ${key} is set (value not shown)"
      found_any=1
    fi
  done
  if [ "$found_any" -eq 0 ]; then
    echo "  (no recognized override keys found in it — see example.env for the list)"
  fi
else
  echo "No .env file — that's fine, docker-compose.yml has safe local-dev defaults built in."
  echo "Only create one if you want to override credentials or ports: cp example.env .env"
fi

echo ""
echo "== Auth token =="
if [ -f .env ] && grep -qE '^APP_AUTH_TOKEN=.+' .env 2>/dev/null; then
  echo "APP_AUTH_TOKEN is set — the API will require it on every request."
else
  echo "REQUIRED, not set: APP_AUTH_TOKEN."
  echo "  Without it the backend refuses every /api/* request (503) and the UI"
  echo "  will never get past its token prompt. Generate one and add it to .env:"
  echo "    openssl rand -hex 32"
  echo "  then add: APP_AUTH_TOKEN=<the generated value>"
fi

echo ""
echo "== Secret key =="
secret_key_value=""
if [ -f .env ]; then
  secret_key_value=$(grep -E '^APP_SECRET_KEY=' .env 2>/dev/null | tail -n1 | cut -d= -f2-)
fi
allow_insecure_value=""
if [ -f .env ]; then
  allow_insecure_value=$(grep -E '^APP_ALLOW_INSECURE_SECRET_KEY=' .env 2>/dev/null | tail -n1 | cut -d= -f2-)
fi
if [ -z "$secret_key_value" ] || [ "$secret_key_value" = "$INSECURE_DEFAULT_SECRET_KEY" ]; then
  if [ "$allow_insecure_value" = "true" ]; then
    echo "APP_SECRET_KEY is still the insecure default, but APP_ALLOW_INSECURE_SECRET_KEY=true"
    echo "  is set, so the backend will start anyway. Fine for a disposable instance only."
  else
    echo "REQUIRED, not set (or still the insecure default): APP_SECRET_KEY."
    echo "  The backend refuses to start until this is a real random value — every stored"
    echo "  credential (target passwords, AI keys, webhook URLs) is encrypted with it."
    echo "    openssl rand -hex 32"
    echo "  then add: APP_SECRET_KEY=<the generated value>"
  fi
else
  echo "APP_SECRET_KEY is set to a non-default value."
fi

echo ""
echo "== Port availability =="
any_busy=0
while IFS='|' read -r port envvar label; do
  [ -z "$port" ] && continue
  if is_port_free "$port"; then
    echo "OK    port ${port} is free — ${label}"
  else
    any_busy=1
    suggestion=$(suggest_free_port "$port")
    echo "BUSY  port ${port} is already in use — ${label}"
    if [ -n "$suggestion" ]; then
      echo "      suggestion: ${envvar}=${suggestion}"
    else
      echo "      no free port found nearby — pick one manually and set ${envvar}"
    fi
  fi
done <<EOF
$PORT_CHECKS
EOF

echo ""
if [ "$any_busy" -eq 0 ]; then
  echo "All checked ports are free. You're good to run: docker compose up"
else
  echo "Some ports are busy. Export the suggested override(s) before starting, e.g.:"
  echo "  export BACKEND_PORT=8001"
  echo "  docker compose up"
  echo "(or add the same KEY=value lines to a .env file instead of exporting them)"
fi

# Keep the window open when this was double-clicked rather than run from an
# already-open terminal (only prompts when stdin is actually interactive).
if [ -t 0 ]; then
  echo ""
  read -r -p "Press Enter to close this window..."
fi
