"""Migration linter (app/ddl_lint.py): parsing, the lock table, the rules
and the live-impact wording. Pure — a fake Catalog stands in for a target."""

from app.ddl_lint import (
    ACCESS_EXCLUSIVE,
    INDEX_BUILD,
    REWRITE,
    SCAN,
    SHARE,
    SHARE_ROW_EXCLUSIVE,
    SHARE_UPDATE_EXCLUSIVE,
    LockUse,
    blocks,
    conflicts,
    describe_impact,
    lint,
)
from app.units import format_bytes, format_seconds


def rules(result):
    return [f["rule"] for f in result.findings]


class FakeCatalog:
    def __init__(self, version=170000, volatility=None, types=None):
        self.version = version
        self.volatility = volatility or {}
        self.types = types or {}

    def server_version(self):
        return self.version

    def function_volatility(self, name):
        return self.volatility.get(name)

    def column_type(self, relation, column):
        return self.types.get((relation[1], column))


def test_lock_conflict_table():
    assert conflicts(ACCESS_EXCLUSIVE, "ACCESS SHARE")
    assert not conflicts(SHARE, "ACCESS SHARE")
    assert conflicts(SHARE, "ROW EXCLUSIVE")
    assert not conflicts(SHARE_UPDATE_EXCLUSIVE, "ROW EXCLUSIVE")
    assert blocks(ACCESS_EXCLUSIVE) == "reads and writes"
    assert blocks(SHARE) == "writes"
    assert blocks(SHARE_ROW_EXCLUSIVE) == "writes"
    assert blocks(SHARE_UPDATE_EXCLUSIVE) == "nothing"


def test_parse_error_has_line_and_column():
    result = lint("SET lock_timeout = '2s';\nALTER TABLE orders ADD COLUM x int;")
    assert result.statements == []
    assert result.parse_error["line"] == 2
    assert "syntax error" in result.parse_error["message"]


def test_statements_carry_their_line_past_leading_comments():
    result = lint("-- first\nSET lock_timeout = '1s';\n\n/* c */\nCREATE INDEX CONCURRENTLY i ON t (a);")
    assert [s.line for s in result.statements] == [2, 5]
    assert result.statements[1].kind == "CREATE INDEX CONCURRENTLY"


def test_index_without_concurrently():
    result = lint("SET lock_timeout = '2s'; CREATE INDEX orders_email_idx ON orders (email);")
    stmt = result.statements[1]
    assert stmt.locks[0].mode == SHARE
    assert stmt.effect == INDEX_BUILD
    assert rules(result) == ["index-not-concurrent"]


def test_index_on_table_created_in_same_migration_is_fine():
    result = lint("CREATE TABLE t (id bigint PRIMARY KEY, a text); CREATE INDEX t_a ON t (a);")
    assert "index-not-concurrent" not in rules(result)
    assert "missing-lock-timeout" not in rules(result)


def test_concurrently_inside_a_transaction_is_an_error():
    result = lint("BEGIN; CREATE INDEX CONCURRENTLY i ON orders (a); COMMIT;")
    assert "cannot-run-in-transaction" in rules(result)
    assert lint("CREATE INDEX CONCURRENTLY i ON orders (a);").findings == []


def test_missing_lock_timeout_reported_once():
    result = lint("ALTER TABLE a DROP CONSTRAINT x; ALTER TABLE b DROP CONSTRAINT y;")
    assert rules(result).count("missing-lock-timeout") == 1
    assert lint("SET lock_timeout = '3s'; ALTER TABLE a DROP CONSTRAINT x;").findings == []
    # Zero means "wait forever".
    assert "missing-lock-timeout" in rules(lint("SET lock_timeout = 0; ALTER TABLE a DROP CONSTRAINT x;"))


def test_set_local_lock_timeout_ends_with_the_transaction():
    sql = "BEGIN; SET LOCAL lock_timeout = '2s'; ALTER TABLE a DROP CONSTRAINT x; COMMIT; ALTER TABLE b DROP CONSTRAINT y;"
    result = lint(sql)
    assert [f["statement_index"] for f in result.findings if f["rule"] == "missing-lock-timeout"] == [5]


def test_add_column_with_volatile_default_rewrites():
    result = lint("SET lock_timeout = '2s'; ALTER TABLE orders ADD COLUMN token uuid DEFAULT gen_random_uuid();")
    assert result.statements[1].effect == REWRITE
    assert rules(result) == ["add-column-rewrite"]


def test_add_column_with_constant_or_stable_default_is_instant():
    for default in ("0", "now()", "'x'", "CURRENT_TIMESTAMP"):
        result = lint(f"SET lock_timeout = '2s'; ALTER TABLE orders ADD COLUMN c text DEFAULT {default};")
        assert result.findings == [], default
        assert result.statements[1].effect == "instant"


def test_unknown_default_function_is_asked_of_the_catalog():
    sql = "SET lock_timeout = '2s'; ALTER TABLE orders ADD COLUMN c text DEFAULT make_code();"
    assert lint(sql).findings == []
    assert "make_code" in lint(sql).statements[1].notes[0]
    assert rules(lint(sql, FakeCatalog(volatility={"make_code": "v"}))) == ["add-column-rewrite"]
    assert lint(sql, FakeCatalog(volatility={"make_code": "s"})).findings == []


def test_add_serial_or_identity_column_rewrites():
    assert "add-column-rewrite" in rules(lint("SET lock_timeout='1s'; ALTER TABLE t ADD COLUMN n bigserial;"))
    assert "add-column-rewrite" in rules(
        lint("SET lock_timeout='1s'; ALTER TABLE t ADD COLUMN n bigint GENERATED ALWAYS AS IDENTITY;")
    )


def test_any_default_rewrites_before_postgresql_11():
    sql = "SET lock_timeout='1s'; ALTER TABLE t ADD COLUMN c int DEFAULT 0;"
    assert rules(lint(sql, FakeCatalog(version=100000))) == ["add-column-rewrite"]


def test_not_null_column_without_default():
    assert "add-column-not-null-without-default" in rules(lint("SET lock_timeout='1s'; ALTER TABLE t ADD COLUMN c int NOT NULL;"))
    assert lint("SET lock_timeout='1s'; ALTER TABLE t ADD COLUMN c int NOT NULL DEFAULT 0;").findings == []


def test_set_not_null_scans():
    result = lint("SET lock_timeout='1s'; ALTER TABLE orders ALTER COLUMN email SET NOT NULL;")
    assert result.statements[1].effect == SCAN
    assert rules(result) == ["set-not-null"]


def test_foreign_key_without_not_valid_locks_both_tables():
    result = lint("SET lock_timeout='1s'; ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (customer_id) REFERENCES customers (id);")
    stmt = result.statements[1]
    assert {(lock.name, lock.mode) for lock in stmt.locks} == {
        ("orders", SHARE_ROW_EXCLUSIVE),
        ("customers", SHARE_ROW_EXCLUSIVE),
    }
    assert stmt.effect == SCAN
    assert rules(result) == ["constraint-without-not-valid"]
    not_valid = lint(
        "SET lock_timeout='1s'; ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (customer_id) REFERENCES customers (id) NOT VALID;"
        " ALTER TABLE orders VALIDATE CONSTRAINT fk;"
    )
    assert not_valid.findings == []
    assert not_valid.statements[2].locks[0].mode == SHARE_UPDATE_EXCLUSIVE


def test_check_constraint_without_not_valid():
    assert rules(lint("SET lock_timeout='1s'; ALTER TABLE t ADD CONSTRAINT c CHECK (a > 0);")) == [
        "constraint-without-not-valid"
    ]


def test_unique_constraint_builds_index_unless_using_index():
    assert rules(lint("SET lock_timeout='1s'; ALTER TABLE t ADD CONSTRAINT u UNIQUE (a);")) == ["add-constraint-builds-index"]
    assert lint("SET lock_timeout='1s'; ALTER TABLE t ADD CONSTRAINT u UNIQUE USING INDEX t_a_idx;").findings == []


def test_alter_column_type_rewrites_unless_binary_coercible():
    sql = "SET lock_timeout='1s'; ALTER TABLE orders ALTER COLUMN note TYPE varchar(200);"
    assert rules(lint(sql)) == ["alter-column-type"]
    widening = FakeCatalog(types={("orders", "note"): "character varying(50)"})
    result = lint(sql, widening)
    assert result.findings == []
    assert result.statements[1].effect == "instant"
    narrowing = FakeCatalog(types={("orders", "note"): "character varying(500)"})
    assert rules(lint(sql, narrowing)) == ["alter-column-type"]
    to_text = "SET lock_timeout='1s'; ALTER TABLE orders ALTER COLUMN note TYPE text;"
    assert lint(to_text, widening).findings == []
    int_to_bigint = "SET lock_timeout='1s'; ALTER TABLE orders ALTER COLUMN id TYPE bigint;"
    assert rules(lint(int_to_bigint, FakeCatalog(types={("orders", "id"): "integer"}))) == ["alter-column-type"]


def test_multiple_subcommands_take_the_strongest_lock():
    result = lint("SET lock_timeout='1s'; ALTER TABLE t SET (fillfactor = 80), ALTER COLUMN a SET STATISTICS 500;")
    assert result.statements[1].locks[0].mode == SHARE_UPDATE_EXCLUSIVE
    result = lint("SET lock_timeout='1s'; ALTER TABLE t SET (fillfactor = 80), DROP CONSTRAINT x;")
    assert result.statements[1].locks[0].mode == ACCESS_EXCLUSIVE


def test_rename_and_drop():
    found = rules(
        lint("SET lock_timeout='1s'; ALTER TABLE t RENAME COLUMN a TO b; ALTER TABLE t DROP COLUMN c; DROP TABLE old_t;")
    )
    assert found == ["rename", "drop-column", "drop-table"]


def test_drop_index_concurrently():
    assert rules(lint("SET lock_timeout='1s'; DROP INDEX public.i;")) == ["drop-index-not-concurrent"]
    result = lint("DROP INDEX CONCURRENTLY public.i;")
    assert result.findings == []
    assert result.statements[0].locks[0].via_index


def test_rewrites_and_refresh():
    assert "rewrite-vacuum-full" in rules(lint("VACUUM FULL orders;"))
    assert "rewrite-cluster" in rules(lint("SET lock_timeout='1s'; CLUSTER orders USING orders_pkey;"))
    assert "rewrite-set-tablespace" in rules(lint("SET lock_timeout='1s'; ALTER TABLE t SET TABLESPACE slow;"))
    assert rules(lint("SET lock_timeout='1s'; REFRESH MATERIALIZED VIEW mv;")) == ["refresh-matview-not-concurrent"]
    assert lint("SET lock_timeout='1s'; REFRESH MATERIALIZED VIEW CONCURRENTLY mv;").statements[1].locks[0].mode == "EXCLUSIVE"


def test_reindex():
    assert rules(lint("SET lock_timeout='1s'; REINDEX TABLE orders;")) == ["reindex-not-concurrent"]
    assert lint("REINDEX TABLE CONCURRENTLY orders;").findings == []


def test_partition_of_locks_parent_and_attach_does_not():
    assert "partition-of-locks-parent" in rules(
        lint("SET lock_timeout='1s'; CREATE TABLE m_2027 PARTITION OF m FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');")
    )
    attach = lint("SET lock_timeout='1s'; ALTER TABLE m ATTACH PARTITION m_2027 FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');")
    modes = {lock.name: lock.mode for lock in attach.statements[1].locks}
    assert modes == {"m": SHARE_UPDATE_EXCLUSIVE, "m_2027": ACCESS_EXCLUSIVE}


def test_backfill_without_where():
    assert rules(lint("UPDATE orders SET status = 'x';")) == ["unbatched-backfill"]
    assert lint("UPDATE orders SET status = 'x' WHERE id < 1000;").findings == []


def test_slow_statement_after_strong_lock_in_one_transaction():
    sql = "BEGIN; SET LOCAL lock_timeout = '1s'; ALTER TABLE a DROP CONSTRAINT x; UPDATE b SET c = 1 WHERE d = 2; COMMIT;"
    assert "lock-held-across-statements" in rules(lint(sql))


def test_create_table_advice():
    found = rules(lint("CREATE TABLE t (id serial PRIMARY KEY, at timestamp);"))
    assert found == ["prefer-bigint-key", "prefer-timestamptz"]


def test_foreign_key_in_create_table_locks_referenced_table():
    result = lint("CREATE TABLE t (id bigint PRIMARY KEY, c bigint REFERENCES customers (id));")
    assert [(lock.name, lock.mode) for lock in result.statements[0].locks] == [("customers", SHARE_ROW_EXCLUSIVE)]


def test_non_ascii_text_keeps_lines():
    result = lint("-- tõlge\nSET lock_timeout = '1s';\nCOMMENT ON TABLE t IS 'ülevaade';\nCREATE INDEX i ON t (a);")
    assert [s.line for s in result.statements] == [2, 3, 4]


def test_describe_impact_queues_behind_long_transaction():
    lock = LockUse("public", "orders", ACCESS_EXCLUSIVE)
    facts = {
        "exists": True,
        "table": "public.orders",
        "total_bytes": 42 * 1024**3,
        "rows_estimate": 120_000_000,
        "holders": [
            {"pid": 7, "mode": "ACCESS SHARE", "granted": True, "application_name": "billing-job", "state": "idle in transaction", "xact_seconds": 252},
            {"pid": 8, "mode": "ROW EXCLUSIVE", "granted": True, "application_name": "api", "state": "active", "xact_seconds": 1},
        ],
        "scans_per_second": 37.0,
        "writes_per_second": 12.5,
    }
    impact = describe_impact(lock, REWRITE, facts, 252)
    assert impact["severity"] == "critical"
    assert impact["conflicting_sessions"] == 2
    assert "42.0 GB" in impact["text"]
    assert "4m12s" in impact["text"]
    assert "billing-job" in impact["text"]
    assert "rewrite" in impact["text"]


def test_describe_impact_for_a_quiet_small_table():
    lock = LockUse(None, "t", SHARE_UPDATE_EXCLUSIVE)
    facts = {"exists": True, "table": "public.t", "total_bytes": 8192, "rows_estimate": 0, "holders": []}
    assert describe_impact(lock, "instant", facts, None)["severity"] == "healthy"
    assert describe_impact(lock, "instant", {"exists": False}, None)["severity"] == "info"


def test_formatting():
    assert format_bytes(42 * 1024**3) == "42.0 GB"
    assert format_bytes(512) == "512 bytes"
    assert format_seconds(252) == "4m12s"
    assert format_seconds(7300) == "2h01m"


def test_array_type_changes_are_not_judged_coercible():
    sql = "SET lock_timeout='1s'; ALTER TABLE t ALTER COLUMN tags TYPE text[];"
    assert rules(lint(sql, FakeCatalog(types={("t", "tags"): "character varying(50)[]"}))) == ["alter-column-type"]
    assert rules(lint(sql, FakeCatalog(types={("t", "tags"): "character varying(50)"}))) == ["alter-column-type"]
    numeric = "SET lock_timeout='1s'; ALTER TABLE t ALTER COLUMN amount TYPE numeric(12,2);"
    assert lint(numeric, FakeCatalog(types={("t", "amount"): "numeric(10,2)"})).findings == []
    assert rules(lint(numeric, FakeCatalog(types={("t", "amount"): "numeric(10,2)[]"}))) == ["alter-column-type"]


def test_index_rename_and_index_attach_are_light():
    rename = lint("ALTER INDEX i RENAME TO j;")
    assert rename.statements[0].locks[0].mode == SHARE_UPDATE_EXCLUSIVE
    assert rename.findings == []
    attach = lint("SET lock_timeout='1s'; ALTER INDEX parent_idx ATTACH PARTITION child_idx;")
    assert attach.statements[1].effect == "instant"
    assert attach.statements[1].notes == []


def test_set_local_outside_a_transaction_does_nothing():
    result = lint("SET LOCAL lock_timeout = '2s'; ALTER TABLE t DROP CONSTRAINT c;")
    assert "missing-lock-timeout" in rules(result)


def test_finding_ids_are_unique_and_locks_deduplicated():
    result = lint("SET lock_timeout='1s'; ALTER TABLE t ADD COLUMN a int NOT NULL, ADD COLUMN b int NOT NULL;")
    ids = [f["id"] for f in result.findings]
    assert len(ids) == 2 and len(set(ids)) == 2
    create = lint("CREATE TABLE x (a bigint REFERENCES acc (id), b bigint REFERENCES acc (id));")
    assert [(lock.name, lock.mode) for lock in create.statements[0].locks] == [("acc", SHARE_ROW_EXCLUSIVE)]
