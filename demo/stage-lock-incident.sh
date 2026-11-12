#!/bin/sh
# Stages a ~90-second lock incident on the demo database, for the Time
# Machine screen to record and replay:
#
#   1. "billing-job" takes a row lock on orders.id = 1 and keeps its
#      transaction open for 90s (a long job holding locks it took early);
#   2. "checkout-api" tries to update the same row and waits behind it;
#   3. "report-job" asks for a SHARE lock on orders and queues behind both.
#
# Run from the repository root while `docker compose --profile demo up` is
# running. Lock snapshots are taken every 15s, so within a minute the
# incident shows up on Monitor → Time Machine; it ends on its own when the
# first session commits.
set -eu

# The same user/database docker compose gave the demo container: from the
# shell, else from .env (only these two keys are read), else the defaults.
env_value() {
    [ -f .env ] && grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '"'"'"
}
DEMO_USER=${DEMO_POSTGRES_USER:-$(env_value DEMO_POSTGRES_USER)}
DEMO_DB=${DEMO_POSTGRES_DB:-$(env_value DEMO_POSTGRES_DB)}
PSQL="docker compose exec -T pgdba-demo-target psql -U ${DEMO_USER:-demo} -d ${DEMO_DB:-demo} -q"

echo "billing-job: locking orders row 1 and holding it for 90s…"
$PSQL -c "SET application_name = 'billing-job'; BEGIN; UPDATE orders SET total = total WHERE id = 1; SELECT pg_sleep(90); COMMIT;" &
sleep 3

echo "checkout-api: updating the same row (waits)…"
$PSQL -c "SET application_name = 'checkout-api'; UPDATE orders SET total = total + 0 WHERE id = 1;" &
sleep 3

echo "report-job: asking for a SHARE lock on orders (queues behind both)…"
$PSQL -c "SET application_name = 'report-job'; BEGIN; LOCK TABLE orders IN SHARE MODE; SELECT count(*) FROM orders; COMMIT;" &

echo "Waiting for the incident to end (about 90s)…"
wait
echo "Done. Open Monitor → Time Machine and pick the lock incident to replay it."
