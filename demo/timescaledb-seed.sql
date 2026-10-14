-- Seed for the TimescaleDB test target (docker-compose.timescaledb.yml).
-- Runs once, on first start of an empty data volume. All data is random and
-- disposable. Includes a few deliberate issues so the TimescaleDB-aware
-- views (Table Health chunk roll-up, Schema Lint, Index Advisor) have
-- something to report.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- ---------------------------------------------------------------------------
-- Plain relational tables
-- ---------------------------------------------------------------------------
CREATE TABLE devices (
    device_id   serial PRIMARY KEY,
    name        text NOT NULL,
    location    text NOT NULL,
    installed   timestamptz NOT NULL DEFAULT now()
);

INSERT INTO devices (name, location, installed)
SELECT 'sensor-' || lpad(g::text, 3, '0'),
       (ARRAY['hall-a','hall-b','warehouse','roof','lab','garage'])[1 + floor(random() * 6)::int],
       now() - (random() * interval '400 days')
FROM generate_series(1, 40) AS g;

-- ---------------------------------------------------------------------------
-- Hypertable 1: sensor_readings — healthy-ish, compressed, 1-day chunks
-- ---------------------------------------------------------------------------
CREATE TABLE sensor_readings (
    ts          timestamptz NOT NULL,
    device_id   int NOT NULL REFERENCES devices (device_id),
    temperature double precision,
    humidity    double precision,
    battery_pct double precision
);

SELECT create_hypertable('sensor_readings', 'ts', chunk_time_interval => interval '1 day');

CREATE INDEX sensor_readings_device_ts_idx ON sensor_readings (device_id, ts DESC);

-- 40 devices x 14 days x one reading per 10 minutes  (~80k rows)
INSERT INTO sensor_readings (ts, device_id, temperature, humidity, battery_pct)
SELECT t,
       d.device_id,
       round((15 + 10 * random() + 5 * sin(extract(epoch FROM t) / 86400 * 2 * pi()))::numeric, 2),
       round((30 + 40 * random())::numeric, 2),
       round((100 - 100 * (extract(epoch FROM t - (now() - interval '14 days')) / 1209600) * random())::numeric, 1)
FROM devices d
CROSS JOIN generate_series(now() - interval '14 days', now(), interval '10 minutes') AS t;

ALTER TABLE sensor_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'ts DESC'
);

-- Compress everything older than 3 days now, and keep doing so automatically.
SELECT compress_chunk(c, if_not_compressed => true)
FROM show_chunks('sensor_readings', older_than => interval '3 days') AS c;
SELECT add_compression_policy('sensor_readings', interval '3 days');
SELECT add_retention_policy('sensor_readings', interval '90 days');

-- ---------------------------------------------------------------------------
-- Continuous aggregate over sensor_readings
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW sensor_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', ts) AS bucket,
       device_id,
       avg(temperature) AS avg_temp,
       max(temperature) AS max_temp,
       avg(humidity)    AS avg_humidity,
       min(battery_pct) AS min_battery
FROM sensor_readings
GROUP BY bucket, device_id
WITH NO DATA;

CALL refresh_continuous_aggregate('sensor_hourly', NULL, NULL);
SELECT add_continuous_aggregate_policy('sensor_hourly',
    start_offset => interval '3 days',
    end_offset => interval '1 hour',
    schedule_interval => interval '1 hour');

-- ---------------------------------------------------------------------------
-- Hypertable 2: app_events — deliberately over-chunked (1-hour chunks over
-- 5 days = ~120 tiny chunks), no compression, no retention policy.
-- ---------------------------------------------------------------------------
CREATE TABLE app_events (
    ts         timestamptz NOT NULL,
    device_id  int,                      -- deliberately no FK / no index
    event_type text NOT NULL,
    payload    jsonb
);

SELECT create_hypertable('app_events', 'ts', chunk_time_interval => interval '1 hour');

INSERT INTO app_events (ts, device_id, event_type, payload)
SELECT now() - (random() * interval '5 days'),
       1 + floor(random() * 40)::int,
       (ARRAY['boot','alert','heartbeat','config_change','error'])[1 + floor(random() * 5)::int],
       jsonb_build_object('code', floor(random() * 500)::int, 'note', md5(random()::text))
FROM generate_series(1, 60000);

-- ---------------------------------------------------------------------------
-- Ordinary tables with deliberate issues
-- ---------------------------------------------------------------------------
-- Float-typed money, unindexed foreign key.
CREATE TABLE maintenance_orders (
    order_id   serial PRIMARY KEY,
    device_id  int NOT NULL REFERENCES devices (device_id),   -- no index on the FK
    cost       double precision NOT NULL,                      -- money as float
    status     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO maintenance_orders (device_id, cost, status, created_at)
SELECT 1 + floor(random() * 40)::int,
       round((random() * 900)::numeric, 2),
       (ARRAY['open','done','cancelled'])[1 + floor(random() * 3)::int],
       now() - (random() * interval '200 days')
FROM generate_series(1, 20000);

-- Bloated, unvacuumed table: autovacuum off, then churn rows.
CREATE TABLE device_status_log (
    id      serial PRIMARY KEY,
    device_id int NOT NULL,
    status  text NOT NULL,
    updated timestamptz NOT NULL DEFAULT now()
) WITH (autovacuum_enabled = false);

INSERT INTO device_status_log (device_id, status)
SELECT 1 + floor(random() * 40)::int, 'ok' FROM generate_series(1, 30000);
UPDATE device_status_log SET status = 'degraded', updated = now() WHERE id % 2 = 0;
UPDATE device_status_log SET status = 'ok', updated = now() WHERE id % 3 = 0;

-- Duplicate indexes.
CREATE INDEX maintenance_orders_status_idx1 ON maintenance_orders (status);
CREATE INDEX maintenance_orders_status_idx2 ON maintenance_orders (status);

ANALYZE;

-- A little query traffic so pg_stat_statements / Query Intelligence has data.
SELECT count(*) FROM sensor_readings WHERE ts > now() - interval '1 day';
SELECT device_id, avg(temperature) FROM sensor_readings GROUP BY device_id;
SELECT * FROM sensor_hourly ORDER BY bucket DESC LIMIT 100;
SELECT event_type, count(*) FROM app_events WHERE device_id = 7 GROUP BY event_type;
SELECT * FROM maintenance_orders WHERE device_id = 12;
