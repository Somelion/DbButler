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
