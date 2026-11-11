-- Demo dataset for pgdba-demo-target — intentionally contains a handful of
-- realistic anti-patterns so a first run of `docker compose --profile demo up`
-- has something for every view to show, rather than empty states.
-- This is NOT a schema-design template — see PostgreDba's own Advisor
-- findings once you connect to this target for what it flags here and why.

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE TABLE customers (
    id         BIGSERIAL PRIMARY KEY,
    email      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deliberately unindexed FK — Index Advisor should flag this.
CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id),
    -- Deliberately float for money — Schema Lint should flag this.
    total       DOUBLE PRECISION NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE order_items (
    id           BIGSERIAL PRIMARY KEY,
    order_id     BIGINT NOT NULL REFERENCES orders(id),
    product_name TEXT NOT NULL,
    quantity     INTEGER NOT NULL,
    -- Deliberately float for money — a second Schema Lint hit.
    unit_price   REAL NOT NULL
);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);

-- Duplicate indexes — Index Advisor should flag these as redundant.
CREATE INDEX idx_customers_email ON customers(email);
CREATE INDEX idx_customers_email_dup ON customers(email);

-- Never queried against below — Index Advisor should flag this as unused.
CREATE INDEX idx_customers_created_at ON customers(created_at);

-- ~5,000 customers, ~20,000 orders, ~40,000 order_items.
INSERT INTO customers (email, created_at)
SELECT
    'customer' || g || '@example.com',
    now() - (random() * interval '365 days')
FROM generate_series(1, 5000) AS g;

INSERT INTO orders (customer_id, total, created_at)
SELECT
    (random() * 4999 + 1)::bigint,
    round((random() * 500 + 5)::numeric, 2),
    now() - (random() * interval '180 days')
FROM generate_series(1, 20000) AS g;

INSERT INTO order_items (order_id, product_name, quantity, unit_price)
SELECT
    (random() * 19999 + 1)::bigint,
    'Product ' || (random() * 50)::int,
    (random() * 4 + 1)::int,
    round((random() * 80 + 5)::numeric, 2)
FROM generate_series(1, 40000) AS g;

ANALYZE customers;
ANALYZE orders;
ANALYZE order_items;

-- Simulate unvacuumed bloat on orders: bulk-rewrite ~40% of rows (still
-- creates a new MVCC row version even though the value is unchanged), then
-- raise this table's autovacuum threshold so the dead tuples stick around
-- long enough to actually see in Table Health / Dashboard. NOT a real-world
-- recommendation — purely so this demo has bloat worth looking at.
UPDATE orders SET total = total * 1.0 WHERE id % 5 IN (0, 1);
ALTER TABLE orders SET (autovacuum_vacuum_scale_factor = 0.9, autovacuum_analyze_scale_factor = 0.9);

-- A handful of representative query patterns so pg_stat_statements — and
-- Query Intelligence / Diagnose Now / Schema Lint's query checks — have
-- real entries to show instead of an empty state.

-- Missing-index lookup, run a few times so it accrues a meaningful call count.
SELECT * FROM orders WHERE customer_id = 42;
SELECT * FROM orders WHERE customer_id = 108;
SELECT * FROM orders WHERE customer_id = 501;

-- SELECT * anti-pattern.
SELECT * FROM order_items WHERE order_id = 1000;

-- Deep OFFSET pagination anti-pattern.
SELECT id, total, created_at FROM orders ORDER BY created_at DESC LIMIT 20 OFFSET 5000;

-- NOT IN with a subquery anti-pattern.
SELECT * FROM customers WHERE id NOT IN (SELECT customer_id FROM orders);

-- Workload Map. Three relationships, one of each kind the map tells apart:
--   customers <- orders       FK, joined below — and the FK is unindexed
--   orders    <- order_items  FK, joined below, indexed
--   orders    <- refunds      joined below on refunds.order_ref, but no FK
--                             declared (the dashed "joined without FK" edge)
--   customers <- customer_notes  FK declared, never joined (dotted edge)
-- The joins write intervals as '60 days'::interval, not interval '60 days':
-- pg_stat_statements normalizes the latter to "interval $1", which no longer
-- parses, so the Workload Map couldn't plan it.
CREATE TABLE refunds (
    id         BIGSERIAL PRIMARY KEY,
    order_ref  BIGINT NOT NULL,          -- deliberately no REFERENCES orders(id)
    amount     NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO refunds (order_ref, amount, created_at)
SELECT (random() * 19999 + 1)::bigint, round((random() * 50 + 1)::numeric, 2), now() - (random() * interval '90 days')
FROM generate_series(1, 1500);

CREATE TABLE customer_notes (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id),
    note        TEXT NOT NULL
);
CREATE INDEX idx_customer_notes_customer_id ON customer_notes(customer_id);
INSERT INTO customer_notes (customer_id, note)
SELECT (random() * 4999 + 1)::bigint, 'Called about order ' || g FROM generate_series(1, 300) AS g;
ANALYZE refunds;
ANALYZE customer_notes;

SELECT c.email, count(o.id) AS orders, sum(o.total) AS spent
FROM customers c JOIN orders o ON o.customer_id = c.id
WHERE c.created_at > now() - '60 days'::interval
GROUP BY c.email ORDER BY spent DESC LIMIT 20;

SELECT o.id, o.created_at, sum(oi.quantity * oi.unit_price) AS order_value
FROM orders o JOIN order_items oi ON oi.order_id = o.id
WHERE o.created_at > now() - '7 days'::interval
GROUP BY o.id, o.created_at;

SELECT o.customer_id, sum(r.amount) AS refunded
FROM refunds r JOIN orders o ON o.id = r.order_ref
WHERE r.created_at > now() - '30 days'::interval
GROUP BY o.customer_id ORDER BY refunded DESC LIMIT 10;

-- A forgotten two-phase transaction: prepared, then never committed or rolled
-- back — what a crashed transaction manager leaves behind. It holds back the
-- vacuum horizon for this database from here on, so the Dashboard's Vacuum
-- Horizon tile has a real holder to show: VACUUM can't remove rows deleted
-- after this point until it's resolved. (The orders bloat above was created
-- before it, so VACUUM still cleans that up.) To clear it:
--   ROLLBACK PREPARED 'demo_forgotten_2pc';
-- Needs max_prepared_transactions > 0 (set in docker-compose.yml); skipped
-- otherwise, so the rest of the seed still works without it.
CREATE TABLE payment_batches (
    id         BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT current_setting('max_prepared_transactions')::int > 0 AS can_prepare \gset
\if :can_prepare
BEGIN;
INSERT INTO payment_batches DEFAULT VALUES;
PREPARE TRANSACTION 'demo_forgotten_2pc';
\endif
