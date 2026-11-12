"""Migration linter: what a pasted migration will lock, for how long, and
what's risky about it — squawk-style rules on PostgreSQL's own parser
(pglast / libpg_query), plus a lock-mode table per statement.

Pure: nothing here talks to a database. The live half — table sizes,
sessions holding conflicting locks right now, traffic — comes in through a
`Catalog` (routers/ddl_lint.py implements it against the target) and is
turned into words by describe_impact().

Lock levels follow the PostgreSQL 18 documentation (ALTER TABLE: "An ACCESS
EXCLUSIVE lock is acquired unless explicitly noted"; explicit-locking's
conflict table for what each mode blocks).
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from pglast import ast, parse_sql
from pglast.enums.parsenodes import (
    AlterTableType,
    ConstrType,
    ObjectType,
    ReindexObjectType,
    TransactionStmtKind,
    VariableSetKind,
)
from pglast.parser import ParseError, split

from app.units import format_bytes, format_seconds

MAX_SQL_CHARS = 200_000
MAX_STATEMENTS = 500
SQL_PREVIEW_CHARS = 300

# --- Lock modes ---------------------------------------------------------------

ACCESS_SHARE = "ACCESS SHARE"
ROW_SHARE = "ROW SHARE"
ROW_EXCLUSIVE = "ROW EXCLUSIVE"
SHARE_UPDATE_EXCLUSIVE = "SHARE UPDATE EXCLUSIVE"
SHARE = "SHARE"
SHARE_ROW_EXCLUSIVE = "SHARE ROW EXCLUSIVE"
EXCLUSIVE = "EXCLUSIVE"
ACCESS_EXCLUSIVE = "ACCESS EXCLUSIVE"

LOCK_STRENGTH = {
    ACCESS_SHARE: 1,
    ROW_SHARE: 2,
    ROW_EXCLUSIVE: 3,
    SHARE_UPDATE_EXCLUSIVE: 4,
    SHARE: 5,
    SHARE_ROW_EXCLUSIVE: 6,
    EXCLUSIVE: 7,
    ACCESS_EXCLUSIVE: 8,
}
MODE_BY_NUMBER = {number: mode for mode, number in LOCK_STRENGTH.items()}

# pg_locks.mode spelling of each mode.
PG_LOCKS_MODE = {
    "AccessShareLock": ACCESS_SHARE,
    "RowShareLock": ROW_SHARE,
    "RowExclusiveLock": ROW_EXCLUSIVE,
    "ShareUpdateExclusiveLock": SHARE_UPDATE_EXCLUSIVE,
    "ShareLock": SHARE,
    "ShareRowExclusiveLock": SHARE_ROW_EXCLUSIVE,
    "ExclusiveLock": EXCLUSIVE,
    "AccessExclusiveLock": ACCESS_EXCLUSIVE,
}

# Table 13.2, "Conflicting Lock Modes".
CONFLICTS = {
    ACCESS_SHARE: {ACCESS_EXCLUSIVE},
    ROW_SHARE: {EXCLUSIVE, ACCESS_EXCLUSIVE},
    ROW_EXCLUSIVE: {SHARE, SHARE_ROW_EXCLUSIVE, EXCLUSIVE, ACCESS_EXCLUSIVE},
    SHARE_UPDATE_EXCLUSIVE: {SHARE_UPDATE_EXCLUSIVE, SHARE, SHARE_ROW_EXCLUSIVE, EXCLUSIVE, ACCESS_EXCLUSIVE},
    SHARE: {ROW_EXCLUSIVE, SHARE_UPDATE_EXCLUSIVE, SHARE_ROW_EXCLUSIVE, EXCLUSIVE, ACCESS_EXCLUSIVE},
    SHARE_ROW_EXCLUSIVE: {ROW_EXCLUSIVE, SHARE_UPDATE_EXCLUSIVE, SHARE, SHARE_ROW_EXCLUSIVE, EXCLUSIVE, ACCESS_EXCLUSIVE},
    EXCLUSIVE: {ROW_SHARE, ROW_EXCLUSIVE, SHARE_UPDATE_EXCLUSIVE, SHARE, SHARE_ROW_EXCLUSIVE, EXCLUSIVE, ACCESS_EXCLUSIVE},
    ACCESS_EXCLUSIVE: set(LOCK_STRENGTH),
}


def conflicts(a: str, b: str) -> bool:
    return b in CONFLICTS[a]


def blocks(mode: str) -> str:
    """What ordinary traffic a lock mode stops: SELECTs take ACCESS SHARE,
    INSERT/UPDATE/DELETE take ROW EXCLUSIVE."""
    if conflicts(mode, ACCESS_SHARE):
        return "reads and writes"
    if conflicts(mode, ROW_EXCLUSIVE):
        return "writes"
    return "nothing"


def stronger(a: str | None, b: str) -> str:
    return b if a is None or LOCK_STRENGTH[b] > LOCK_STRENGTH[a] else a


# --- Effects: how long the lock is held ------------------------------------------

INSTANT, ROWS, SCAN, INDEX_BUILD, REWRITE = "instant", "rows", "scan", "index_build", "rewrite"
EFFECT_ORDER = {None: 0, INSTANT: 1, ROWS: 2, SCAN: 3, INDEX_BUILD: 4, REWRITE: 5}
EFFECT_LABELS = {
    INSTANT: "Catalog change only — quick once the lock is granted",
    ROWS: "Changes rows one by one; time grows with the rows touched",
    SCAN: "Reads the whole table while holding the lock",
    INDEX_BUILD: "Builds an index; time grows with the table size",
    REWRITE: "Rewrites the whole table and its indexes while holding the lock",
}
SLOW_EFFECTS = {ROWS, SCAN, INDEX_BUILD, REWRITE}


def worse_effect(a: str | None, b: str | None) -> str | None:
    return b if EFFECT_ORDER[b] > EFFECT_ORDER[a] else a


# Functions known to be volatile, by name. A column added with a volatile
# default rewrites the table; a non-volatile one (now(), a constant) is a
# catalog-only change since PostgreSQL 11. Anything else is asked of the
# target (Catalog.function_volatility) when one is connected.
KNOWN_VOLATILE_FUNCTIONS = frozenset(
    {
        "random",
        "random_normal",
        "setseed",
        "clock_timestamp",
        "timeofday",
        "gen_random_uuid",
        "uuidv4",
        "uuidv7",
        "uuid_generate_v1",
        "uuid_generate_v1mc",
        "uuid_generate_v4",
        "nextval",
        "txid_current",
        "pg_current_xact_id",
    }
)
SERIAL_TYPES = frozenset({"serial", "serial4", "bigserial", "serial8", "smallserial", "serial2"})


class Catalog(Protocol):
    """What the linter may ask of a connected target. Every method returns
    None when it doesn't know (no target, object not found)."""

    def server_version(self) -> int | None: ...

    def function_volatility(self, name: str) -> str | None: ...  # 'i' | 's' | 'v'

    def column_type(self, relation: tuple[str | None, str], column: str) -> str | None: ...


class _NoCatalog:
    def server_version(self) -> int | None:
        return None

    def function_volatility(self, name: str) -> str | None:
        return None

    def column_type(self, relation, column) -> str | None:
        return None


# --- Results ---------------------------------------------------------------------


@dataclass
class LockUse:
    schema: str | None
    name: str
    mode: str
    # The name is an index; the lock is (also) on its table.
    via_index: bool = False

    @property
    def label(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def as_dict(self) -> dict:
        return {
            "relation": self.label,
            "schema": self.schema,
            "name": self.name,
            "mode": self.mode,
            "blocks": blocks(self.mode),
            "via_index": self.via_index,
        }


@dataclass
class StatementResult:
    index: int
    line: int
    sql: str
    kind: str
    locks: list[LockUse] = field(default_factory=list)
    effect: str | None = None
    in_transaction: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "line": self.line,
            "sql": self.sql if len(self.sql) <= SQL_PREVIEW_CHARS else self.sql[: SQL_PREVIEW_CHARS - 1] + "…",
            "kind": self.kind,
            "locks": [lock.as_dict() for lock in self.locks],
            "effect": self.effect,
            "effect_label": EFFECT_LABELS.get(self.effect),
            "in_transaction": self.in_transaction,
            "notes": self.notes,
            "impacts": [],
        }


@dataclass
class LintResult:
    statements: list[StatementResult]
    findings: list[dict]
    parse_error: dict | None = None


# --- AST helpers -------------------------------------------------------------------


def _walk(node):
    """Every AST node under `node` (inclusive)."""
    if isinstance(node, tuple | list):
        for item in node:
            yield from _walk(item)
        return
    if not isinstance(node, ast.Node):
        return
    yield node
    for attribute in node:
        yield from _walk(getattr(node, attribute))


def _strings(names) -> list[str]:
    return [n.sval for n in names or () if isinstance(n, ast.String)]


def _rel(rangevar) -> tuple[str | None, str]:
    return (rangevar.schemaname, rangevar.relname)


def _type_name(type_name) -> str:
    """The type's last name part (int4, varchar, serial, ...)."""
    names = _strings(type_name.names) if type_name is not None else []
    return names[-1].lower() if names else ""


def _function_names(expr) -> list[str]:
    return [_strings(n.funcname)[-1].lower() for n in _walk(expr) if isinstance(n, ast.FuncCall) and n.funcname]


def _defelem_names(options) -> set[str]:
    return {o.defname.lower() for o in options or () if isinstance(o, ast.DefElem) and o.defname}


def _const_text(arg) -> str | None:
    if isinstance(arg, ast.A_Const) and arg.val is not None:
        val = arg.val
        if isinstance(val, ast.String):
            return val.sval
        if isinstance(val, ast.Integer):
            return str(val.ival)
        if isinstance(val, ast.Float):
            return val.fval
    if isinstance(arg, ast.TypeCast):
        return _const_text(arg.arg)
    return None


def _line_of(sql: str, index: int) -> tuple[int, int]:
    line = sql.count("\n", 0, index) + 1
    column = index - (sql.rfind("\n", 0, index) + 1) + 1
    return line, column


def _first_token(text: str) -> int:
    """Offset of the statement's first token past leading comments."""
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
        elif text.startswith("--", i):
            newline = text.find("\n", i)
            i = n if newline == -1 else newline + 1
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = n if close == -1 else close + 2
        else:
            return i
    return 0


# --- The linter --------------------------------------------------------------------


class _State:
    """What earlier statements of the migration established."""

    def __init__(self):
        self.in_transaction = False
        self.lock_timeout = False
        self.lock_timeout_local = False
        self.created: set[tuple[str | None, str]] = set()
        # Strong locks taken inside the open transaction: held until COMMIT.
        self.held: list[tuple[LockUse, int]] = []
        self.warned_lock_timeout = False
        self.warned_held = False

    def is_new(self, schema: str | None, name: str) -> bool:
        """Created earlier in this migration, so still empty and unused. An
        unqualified name matches whichever schema the other side names."""
        return any(n == name and (schema is None or s is None or s == schema) for s, n in self.created)


class _Linter:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.state = _State()
        self.findings: list[dict] = []
        self.version = catalog.server_version()

    # Findings ---------------------------------------------------------------

    def finding(self, stmt: StatementResult, rule: str, severity: str, title: str, detail: str, action: str, **extra):
        # One statement can trip the same rule twice (two subcommands).
        base_id = f"migration-{rule}-{stmt.index}"
        repeats = sum(1 for f in self.findings if f["id"] == base_id or f["id"].startswith(f"{base_id}-"))
        self.findings.append(
            {
                "id": base_id if repeats == 0 else f"{base_id}-{repeats + 1}",
                "rule": rule,
                "category": "migration lint",
                "severity": severity,
                "title": title,
                "summary": f"Statement {stmt.index} (line {stmt.line}): {title[0].lower()}{title[1:]}.",
                "detail": detail,
                "suggested_action": action,
                "statement_index": stmt.index,
                "line": stmt.line,
                **extra,
            }
        )

    # Dispatch ---------------------------------------------------------------

    def run(self, stmt: StatementResult, node) -> None:
        handler = getattr(self, f"_{type(node).__name__}", None)
        stmt.in_transaction = self.state.in_transaction
        if handler is not None:
            handler(stmt, node)
        self._after(stmt)

    def _after(self, stmt: StatementResult) -> None:
        # Two REFERENCES to the same table, say, are one lock.
        unique: dict[tuple, LockUse] = {}
        for lock in stmt.locks:
            unique.setdefault((lock.schema, lock.name, lock.mode, lock.via_index), lock)
        stmt.locks = list(unique.values())
        existing =[lock for lock in stmt.locks if not self.state.is_new(lock.schema, lock.name)]
        traffic_blocking = [lock for lock in existing if blocks(lock.mode) != "nothing"]

        if traffic_blocking and not self.state.lock_timeout and not self.state.warned_lock_timeout:
            self.state.warned_lock_timeout = True
            lock = traffic_blocking[0]
            self.finding(
                stmt,
                "missing-lock-timeout",
                "attention",
                "No lock_timeout before a blocking lock",
                f"This statement needs {lock.mode} on {lock.label}. If any transaction already holds a "
                "conflicting lock — even a long SELECT — the statement waits in the lock queue, and every "
                f"query on {lock.label} that arrives after it queues behind it, so a quick DDL can stall the "
                "table for as long as the oldest transaction runs.",
                "Set a short lock_timeout at the start of the migration and retry on failure, e.g. "
                "SET lock_timeout = '2s'; — failing fast is cheaper than blocking production traffic.",
            )

        if self.state.in_transaction:
            if stmt.effect in SLOW_EFFECTS and not self.state.warned_held:
                earlier = [
                    (lock, index)
                    for lock, index in self.state.held
                    if index != stmt.index and blocks(lock.mode) != "nothing" and not self.state.is_new(lock.schema, lock.name)
                ]
                if earlier:
                    self.state.warned_held = True
                    lock, index = earlier[0]
                    self.finding(
                        stmt,
                        "lock-held-across-statements",
                        "attention",
                        "Slow statement while an earlier lock is held",
                        f"Statement {index} took {lock.mode} on {lock.label} inside the same transaction. "
                        "PostgreSQL keeps every lock until COMMIT, so that table stays blocked "
                        f"({blocks(lock.mode)}) for as long as this slower statement runs too.",
                        "Commit the quick, strongly-locking statements separately from the slow ones, or run the "
                        "slow work (backfills, validations, index builds) in its own transaction.",
                    )
            self.state.held.extend((lock, stmt.index) for lock in existing if LOCK_STRENGTH[lock.mode] >= LOCK_STRENGTH[SHARE])

    def _require_no_transaction(self, stmt: StatementResult, what: str) -> None:
        if self.state.in_transaction:
            self.finding(
                stmt,
                "cannot-run-in-transaction",
                "critical",
                f"{what} can't run inside a transaction block",
                f"{what} is inside BEGIN … COMMIT here, so PostgreSQL rejects it with an error and the "
                "whole migration rolls back. Many migration tools also wrap each file in a transaction.",
                "Run this statement on its own, outside BEGIN/COMMIT (most migration tools have a "
                "per-migration \"no transaction\" switch).",
            )

    # Transaction control and settings -------------------------------------

    def _TransactionStmt(self, stmt, node):
        stmt.kind = "TRANSACTION"
        if node.kind in (TransactionStmtKind.TRANS_STMT_BEGIN, TransactionStmtKind.TRANS_STMT_START):
            self.state.in_transaction = True
            stmt.in_transaction = True
        elif node.kind in (
            TransactionStmtKind.TRANS_STMT_COMMIT,
            TransactionStmtKind.TRANS_STMT_ROLLBACK,
            TransactionStmtKind.TRANS_STMT_PREPARE,
        ):
            self.state.in_transaction = False
            self.state.held = []
            if self.state.lock_timeout_local:
                self.state.lock_timeout = False
                self.state.lock_timeout_local = False

    def _VariableSetStmt(self, stmt, node):
        stmt.kind = "SET"
        if (node.name or "").lower() != "lock_timeout":
            return
        if node.is_local and not self.state.in_transaction:
            # PostgreSQL ignores SET LOCAL outside a transaction block (with
            # a warning), so it changes nothing here either.
            stmt.notes.append("SET LOCAL outside BEGIN … COMMIT has no effect.")
            return
        if node.kind == VariableSetKind.VAR_SET_VALUE and node.args:
            value = (_const_text(node.args[0]) or "").strip().lower()
            enabled = value not in ("", "0", "0s", "0ms")
        else:
            enabled = False  # RESET / DEFAULT: back to the server default, usually 0
        self.state.lock_timeout = enabled
        self.state.lock_timeout_local = enabled and bool(node.is_local)

    # CREATE ------------------------------------------------------------------

    def _CreateStmt(self, stmt, node):
        stmt.kind = "CREATE TABLE"
        stmt.effect = INSTANT
        relation = _rel(node.relation)
        if node.partbound is not None and node.inhRelations:
            parent = node.inhRelations[0]
            stmt.locks.append(LockUse(parent.schemaname, parent.relname, ACCESS_EXCLUSIVE))
            if not self.state.is_new(parent.schemaname, parent.relname):
                self.finding(
                    stmt,
                    "partition-of-locks-parent",
                    "attention",
                    "CREATE TABLE … PARTITION OF locks the whole parent",
                    f"Creating a partition with PARTITION OF takes ACCESS EXCLUSIVE on {parent.relname}, "
                    "blocking every read and write on the partitioned table while it waits and runs.",
                    "Create the table on its own (CREATE TABLE … (LIKE parent INCLUDING ALL)), add a CHECK "
                    "constraint matching the partition bounds, then ALTER TABLE parent ATTACH PARTITION — "
                    "attaching only needs SHARE UPDATE EXCLUSIVE on the parent.",
                )
        for element in node.tableElts or ():
            constraints = []
            if isinstance(element, ast.ColumnDef):
                constraints = list(element.constraints or ())
                self._column_type_advice(stmt, element)
            elif isinstance(element, ast.Constraint):
                constraints = [element]
            for constraint in constraints:
                if constraint.contype == ConstrType.CONSTR_FOREIGN and constraint.pktable is not None:
                    stmt.locks.append(LockUse(constraint.pktable.schemaname, constraint.pktable.relname, SHARE_ROW_EXCLUSIVE))
        self.state.created.add(relation)

    def _column_type_advice(self, stmt, column) -> None:
        type_name = _type_name(column.typeName)
        is_pk = any(c.contype == ConstrType.CONSTR_PRIMARY for c in column.constraints or ())
        if is_pk and type_name in ("int4", "int2", "serial", "serial4", "smallserial", "serial2"):
            self.finding(
                stmt,
                "prefer-bigint-key",
                "info",
                f"Primary key {column.colname} is a 32-bit integer",
                "A 32-bit key runs out at about 2.1 billion values; widening it later is a full table "
                "rewrite under ACCESS EXCLUSIVE, on what is by then a big table.",
                "Use bigint (or bigint GENERATED ALWAYS AS IDENTITY) for surrogate keys.",
            )
        if type_name == "timestamp":
            self.finding(
                stmt,
                "prefer-timestamptz",
                "info",
                f"Column {column.colname} is timestamp without time zone",
                "timestamp stores no time zone, so values written from sessions or servers with "
                "different TimeZone settings can't be compared reliably.",
                "Use timestamptz unless the column really holds a wall-clock time with no zone.",
            )

    def _CreateTableAsStmt(self, stmt, node):
        stmt.kind = "CREATE TABLE AS" if node.objtype == ObjectType.OBJECT_TABLE else "CREATE MATERIALIZED VIEW"
        stmt.effect = ROWS
        if node.into is not None and node.into.rel is not None:
            self.state.created.add(_rel(node.into.rel))

    def _IndexStmt(self, stmt, node):
        stmt.kind = "CREATE INDEX"
        stmt.effect = INDEX_BUILD
        relation = _rel(node.relation)
        if node.concurrent:
            stmt.kind = "CREATE INDEX CONCURRENTLY"
            stmt.locks.append(LockUse(*relation, SHARE_UPDATE_EXCLUSIVE))
            stmt.notes.append(
                "Waits for every transaction that could see the table to finish before it starts and between "
                "its phases; a failed build leaves an INVALID index behind to drop."
            )
            self._require_no_transaction(stmt, "CREATE INDEX CONCURRENTLY")
            return
        stmt.locks.append(LockUse(*relation, SHARE))
        if not self.state.is_new(*relation):
            self.finding(
                stmt,
                "index-not-concurrent",
                "attention",
                "Index built without CONCURRENTLY",
                f"CREATE INDEX takes SHARE on {node.relation.relname}: every INSERT, UPDATE and DELETE on it "
                "waits until the whole index is built — minutes to hours on a big table.",
                "Use CREATE INDEX CONCURRENTLY (outside a transaction block). It takes longer but lets writes "
                "continue; on a partitioned table, build it CONCURRENTLY on each partition, create the parent "
                "index ON ONLY the parent, then ATTACH each partition's index.",
            )

    def _CreateTrigStmt(self, stmt, node):
        stmt.kind = "CREATE TRIGGER"
        stmt.effect = INSTANT
        stmt.locks.append(LockUse(*_rel(node.relation), SHARE_ROW_EXCLUSIVE))

    # ALTER TABLE -------------------------------------------------------------

    def _AlterTableStmt(self, stmt, node):
        stmt.kind = {
            ObjectType.OBJECT_INDEX: "ALTER INDEX",
            ObjectType.OBJECT_MATVIEW: "ALTER MATERIALIZED VIEW",
            ObjectType.OBJECT_VIEW: "ALTER VIEW",
        }.get(node.objtype, "ALTER TABLE")
        relation = _rel(node.relation)
        is_new = self.state.is_new(*relation)
        mode: str | None = None
        for cmd in node.cmds or ():
            cmd_mode, effect = self._alter_cmd(stmt, node, cmd, relation, is_new)
            mode = stronger(mode, cmd_mode)
            stmt.effect = worse_effect(stmt.effect, effect)
        if mode is not None:
            stmt.locks.insert(0, LockUse(*relation, mode, via_index=node.objtype == ObjectType.OBJECT_INDEX))

    def _alter_cmd(self, stmt, node, cmd, relation, is_new) -> tuple[str, str]:
        t = AlterTableType
        sub = cmd.subtype
        table = node.relation.relname

        if sub == t.AT_AddColumn:
            return ACCESS_EXCLUSIVE, self._add_column(stmt, cmd.def_, table, is_new)
        if sub == t.AT_DropColumn:
            if not is_new:
                self.finding(
                    stmt,
                    "drop-column",
                    "info",
                    f"Drops column {cmd.name}",
                    "Dropping a column is quick, but any running application code that still reads or writes "
                    "it starts failing the moment this commits, and the data is gone.",
                    "Deploy code that no longer uses the column first, then drop it in a later migration.",
                )
            return ACCESS_EXCLUSIVE, INSTANT
        if sub in (t.AT_ColumnDefault, t.AT_CookedColumnDefault, t.AT_DropNotNull, t.AT_DropExpression):
            return ACCESS_EXCLUSIVE, INSTANT
        if sub == t.AT_SetNotNull:
            if not is_new:
                self.finding(
                    stmt,
                    "set-not-null",
                    "attention",
                    f"SET NOT NULL on {cmd.name} scans the table under ACCESS EXCLUSIVE",
                    f"PostgreSQL reads every row of {table} to check for NULLs while blocking all reads and "
                    "writes on it — unless a valid CHECK (… IS NOT NULL) constraint already proves it.",
                    f"Add CHECK ({cmd.name} IS NOT NULL) NOT VALID, then VALIDATE CONSTRAINT (which only takes "
                    "SHARE UPDATE EXCLUSIVE), then SET NOT NULL (now catalog-only), then drop the CHECK. On "
                    "PostgreSQL 18, ADD CONSTRAINT … NOT NULL … NOT VALID plus VALIDATE does the same directly.",
                )
            return ACCESS_EXCLUSIVE, SCAN if not is_new else INSTANT
        if sub in (t.AT_SetStatistics, t.AT_SetOptions, t.AT_ResetOptions, t.AT_ClusterOn, t.AT_DropCluster):
            return SHARE_UPDATE_EXCLUSIVE, INSTANT
        if sub in (t.AT_SetRelOptions, t.AT_ResetRelOptions):
            return SHARE_UPDATE_EXCLUSIVE, INSTANT
        if sub == t.AT_AlterColumnType:
            return ACCESS_EXCLUSIVE, self._alter_type(stmt, cmd, relation, table, is_new)
        if sub == t.AT_SetExpression:
            return ACCESS_EXCLUSIVE, self._rewrite(stmt, "set-expression", "Changing a generated column's expression", table, is_new)
        if sub == t.AT_AddConstraint:
            return self._add_constraint(stmt, cmd.def_, table, is_new)
        if sub == t.AT_ValidateConstraint:
            return SHARE_UPDATE_EXCLUSIVE, SCAN
        if sub in (t.AT_SetLogged, t.AT_SetUnLogged):
            return ACCESS_EXCLUSIVE, self._rewrite(stmt, "set-logged", "SET LOGGED / UNLOGGED", table, is_new)
        if sub == t.AT_SetAccessMethod:
            return ACCESS_EXCLUSIVE, self._rewrite(stmt, "set-access-method", "SET ACCESS METHOD", table, is_new)
        if sub == t.AT_SetTableSpace:
            return ACCESS_EXCLUSIVE, self._rewrite(stmt, "set-tablespace", "SET TABLESPACE (copies every data file)", table, is_new)
        if t.AT_EnableTrig <= sub <= t.AT_DisableTrigUser:
            return SHARE_ROW_EXCLUSIVE, INSTANT
        if sub == t.AT_AttachPartition and node.objtype == ObjectType.OBJECT_INDEX:
            # ALTER INDEX parent ATTACH PARTITION child_index: a catalog link
            # between two existing indexes, no scan.
            return SHARE_UPDATE_EXCLUSIVE, INSTANT
        if sub == t.AT_AttachPartition:
            partition = cmd.def_.name if isinstance(cmd.def_, ast.PartitionCmd) else None
            if partition is not None:
                stmt.locks.append(LockUse(partition.schemaname, partition.relname, ACCESS_EXCLUSIVE))
                stmt.notes.append(
                    f"Scans {partition.relname} to check its rows fit the partition bounds, unless a valid CHECK "
                    "constraint already proves it; a default partition is scanned too."
                )
            return SHARE_UPDATE_EXCLUSIVE, SCAN
        if sub == t.AT_DetachPartition:
            concurrent = isinstance(cmd.def_, ast.PartitionCmd) and bool(cmd.def_.concurrent)
            if concurrent:
                self._require_no_transaction(stmt, "DETACH PARTITION CONCURRENTLY")
                return SHARE_UPDATE_EXCLUSIVE, INSTANT
            if not is_new:
                self.finding(
                    stmt,
                    "detach-partition-not-concurrent",
                    "info",
                    "DETACH PARTITION without CONCURRENTLY",
                    f"Takes ACCESS EXCLUSIVE on the partitioned table {table}, blocking every query on it.",
                    "On PostgreSQL 14+, use DETACH PARTITION … CONCURRENTLY (outside a transaction block).",
                )
            return ACCESS_EXCLUSIVE, INSTANT
        if sub == t.AT_DetachPartitionFinalize:
            return SHARE_UPDATE_EXCLUSIVE, INSTANT
        # Everything else (DROP CONSTRAINT, OWNER TO, REPLICA IDENTITY, SET
        # STORAGE, identity changes, RLS, INHERIT, ...) is the documented
        # default: ACCESS EXCLUSIVE, catalog only.
        return ACCESS_EXCLUSIVE, INSTANT

    def _rewrite(self, stmt, rule: str, what: str, table: str, is_new: bool) -> str:
        if not is_new:
            self.finding(
                stmt,
                f"rewrite-{rule}",
                "critical",
                f"{what} rewrites {table}",
                f"{what} rewrites the whole table and its indexes while holding ACCESS EXCLUSIVE — no "
                "reads or writes for the duration, which grows with the table size.",
                "Schedule it in a maintenance window, or use an online approach (a new table filled in "
                "batches plus a swap, or pg_repack) for a big, busy table.",
            )
        return REWRITE

    def _add_column(self, stmt, column, table: str, is_new: bool) -> str:
        if not isinstance(column, ast.ColumnDef) or is_new:
            return INSTANT
        type_name = _type_name(column.typeName)
        constraints = list(column.constraints or ())
        reasons = []
        if type_name in SERIAL_TYPES:
            reasons.append(f"its type {type_name} fills every existing row from a sequence (nextval)")
        has_default = False
        for constraint in constraints:
            if constraint.contype == ConstrType.CONSTR_DEFAULT:
                has_default = True
                volatile, unknown = self._volatility(constraint.raw_expr)
                if volatile:
                    reasons.append(f"its default calls the volatile function {volatile}()")
                elif self.version is not None and self.version < 110000:
                    reasons.append("before PostgreSQL 11 any default fills every existing row")
                elif unknown:
                    stmt.notes.append(
                        f"The default calls {', '.join(unknown)}(): if that is VOLATILE, adding the column "
                        "rewrites the table. Connect a target to have it checked."
                    )
            elif constraint.contype == ConstrType.CONSTR_IDENTITY:
                reasons.append("an identity column is filled for every existing row")
            elif constraint.contype == ConstrType.CONSTR_GENERATED and constraint.generated_kind != "v":
                reasons.append("a stored generated column is computed for every existing row")
        effect = INSTANT
        if reasons:
            effect = REWRITE
            self.finding(
                stmt,
                "add-column-rewrite",
                "critical",
                f"Adding column {column.colname} rewrites {table}",
                f"The column can't be added as a catalog-only change because {'; '.join(reasons)}. The whole "
                "table and its indexes are rewritten under ACCESS EXCLUSIVE — no reads or writes until done.",
                "Add the column without the default (or with a constant default), then backfill the values "
                "in batches and set the default afterwards. For a new key column, add it nullable, backfill, "
                "then add the constraint.",
            )
        is_not_null = bool(column.is_not_null) or any(c.contype == ConstrType.CONSTR_NOTNULL for c in constraints)
        if is_not_null and not has_default and type_name not in SERIAL_TYPES and not any(
            c.contype in (ConstrType.CONSTR_IDENTITY, ConstrType.CONSTR_GENERATED) for c in constraints
        ):
            self.finding(
                stmt,
                "add-column-not-null-without-default",
                "critical",
                f"NOT NULL column {column.colname} added without a default",
                f"Every existing row of {table} would get NULL in a NOT NULL column, so this fails with an "
                "error unless the table is empty.",
                "Add the column with a constant DEFAULT, or add it nullable, backfill, then SET NOT NULL "
                "via a validated CHECK constraint.",
            )
        for constraint in constraints:
            if constraint.contype in (ConstrType.CONSTR_PRIMARY, ConstrType.CONSTR_UNIQUE):
                effect = worse_effect(effect, INDEX_BUILD)
            elif constraint.contype == ConstrType.CONSTR_FOREIGN and constraint.pktable is not None:
                stmt.locks.append(LockUse(constraint.pktable.schemaname, constraint.pktable.relname, SHARE_ROW_EXCLUSIVE))
            elif constraint.contype == ConstrType.CONSTR_CHECK:
                effect = worse_effect(effect, SCAN)
        return effect

    def _volatility(self, expr) -> tuple[str | None, list[str]]:
        """(first volatile function, functions of unknown volatility)."""
        unknown = []
        for name in _function_names(expr):
            if name in KNOWN_VOLATILE_FUNCTIONS:
                return name, []
            volatility = self.catalog.function_volatility(name)
            if volatility == "v":
                return name, []
            if volatility is None:
                unknown.append(name)
        return None, unknown

    def _alter_type(self, stmt, cmd, relation, table: str, is_new: bool) -> str:
        if is_new:
            return INSTANT
        new_type = cmd.def_.typeName if isinstance(cmd.def_, ast.ColumnDef) else None
        new_name = _type_name(new_type)
        current = self.catalog.column_type(relation, cmd.name)
        if current is not None and _binary_coercible(current, new_name, new_type):
            stmt.notes.append(
                f"{cmd.name} is {current} today: changing it to {new_name} needs no rewrite (binary coercible), "
                "just a brief ACCESS EXCLUSIVE lock."
            )
            return INSTANT
        current_text = f" (currently {current})" if current else ""
        self.finding(
            stmt,
            "alter-column-type",
            "critical",
            f"Changing the type of {cmd.name}{current_text} rewrites {table}",
            "ALTER COLUMN TYPE normally rewrites the whole table and rebuilds its indexes under ACCESS "
            "EXCLUSIVE. Only binary-coercible changes skip it (varchar(n) to a longer varchar or to text, "
            "for example).",
            "Add a new column of the new type, backfill it in batches, keep both in sync (trigger or "
            "application), then switch over and drop the old column.",
        )
        return REWRITE

    def _add_constraint(self, stmt, constraint, table: str, is_new: bool) -> tuple[str, str]:
        if not isinstance(constraint, ast.Constraint):
            return ACCESS_EXCLUSIVE, INSTANT
        not_valid = bool(constraint.skip_validation)
        name = constraint.conname or "the constraint"
        if constraint.contype == ConstrType.CONSTR_FOREIGN:
            if constraint.pktable is not None:
                stmt.locks.append(LockUse(constraint.pktable.schemaname, constraint.pktable.relname, SHARE_ROW_EXCLUSIVE))
            if not not_valid and not is_new:
                self._not_valid_finding(stmt, name, table, "foreign key", SHARE_ROW_EXCLUSIVE, "writes on both tables")
            return SHARE_ROW_EXCLUSIVE, INSTANT if not_valid or is_new else SCAN
        if constraint.contype in (ConstrType.CONSTR_CHECK, ConstrType.CONSTR_NOTNULL):
            if not not_valid and not is_new:
                kind = "CHECK" if constraint.contype == ConstrType.CONSTR_CHECK else "NOT NULL"
                self._not_valid_finding(stmt, name, table, f"{kind} constraint", ACCESS_EXCLUSIVE, "all reads and writes")
            return ACCESS_EXCLUSIVE, INSTANT if not_valid or is_new else SCAN
        if constraint.contype in (ConstrType.CONSTR_PRIMARY, ConstrType.CONSTR_UNIQUE, ConstrType.CONSTR_EXCLUSION):
            if constraint.indexname:  # ... USING INDEX: the index already exists
                if constraint.contype == ConstrType.CONSTR_PRIMARY:
                    stmt.notes.append(
                        "PRIMARY KEY … USING INDEX still scans the table to SET NOT NULL on any key column "
                        "not already marked NOT NULL."
                    )
                return ACCESS_EXCLUSIVE, INSTANT
            if not is_new:
                kind = {ConstrType.CONSTR_PRIMARY: "PRIMARY KEY", ConstrType.CONSTR_UNIQUE: "UNIQUE"}.get(
                    constraint.contype, "EXCLUDE"
                )
                self.finding(
                    stmt,
                    "add-constraint-builds-index",
                    "critical",
                    f"Adding {kind} builds its index under ACCESS EXCLUSIVE",
                    f"The index behind the constraint is built while {table} is locked against every read "
                    "and write — the whole build, not just the catalog change.",
                    f"CREATE UNIQUE INDEX CONCURRENTLY first, then ALTER TABLE {table} ADD CONSTRAINT … "
                    f"{kind} USING INDEX <index> (a quick catalog change).",
                )
            return ACCESS_EXCLUSIVE, INDEX_BUILD if not is_new else INSTANT
        return ACCESS_EXCLUSIVE, INSTANT

    def _not_valid_finding(self, stmt, name: str, table: str, kind: str, mode: str, blocked: str) -> None:
        self.finding(
            stmt,
            "constraint-without-not-valid",
            "attention",
            f"New {kind} {name} is validated while locked",
            f"Adding a {kind} checks every existing row of {table} while holding {mode}, blocking "
            f"{blocked} for the whole scan.",
            f"Add it with NOT VALID (a quick catalog change; new rows are still checked), then run "
            f"ALTER TABLE {table} VALIDATE CONSTRAINT {name} separately — validating only takes SHARE "
            "UPDATE EXCLUSIVE, so normal traffic continues.",
        )

    # Other DDL ---------------------------------------------------------------

    def _RenameStmt(self, stmt, node):
        stmt.kind = "RENAME"
        if node.relation is None:
            return
        relation = _rel(node.relation)
        stmt.effect = INSTANT
        # Renaming an index only takes SHARE UPDATE EXCLUSIVE (PostgreSQL 12+).
        is_index = node.renameType == ObjectType.OBJECT_INDEX
        mode = SHARE_UPDATE_EXCLUSIVE if is_index and (self.version is None or self.version >= 120000) else ACCESS_EXCLUSIVE
        stmt.locks.append(LockUse(*relation, mode, via_index=is_index))
        if node.renameType in (ObjectType.OBJECT_TABLE, ObjectType.OBJECT_COLUMN) and not self.state.is_new(*relation):
            what = f"column {node.subname}" if node.renameType == ObjectType.OBJECT_COLUMN else f"table {node.relation.relname}"
            self.finding(
                stmt,
                "rename",
                "attention",
                f"Renames {what}",
                "The rename itself is quick, but application code still using the old name fails as soon as "
                "this commits — a rolling deploy runs old and new code side by side.",
                "Expand and contract: add the new name (a view, or a new column kept in sync), move the code "
                "over, and remove the old name in a later migration.",
            )

    def _DropStmt(self, stmt, node):
        remove = node.removeType
        if remove == ObjectType.OBJECT_INDEX:
            stmt.kind = "DROP INDEX CONCURRENTLY" if node.concurrent else "DROP INDEX"
            stmt.effect = INSTANT
            mode = SHARE_UPDATE_EXCLUSIVE if node.concurrent else ACCESS_EXCLUSIVE
            for names in node.objects or ():
                parts = _strings(names)
                if parts:
                    stmt.locks.append(LockUse(parts[-2] if len(parts) > 1 else None, parts[-1], mode, via_index=True))
            if node.concurrent:
                self._require_no_transaction(stmt, "DROP INDEX CONCURRENTLY")
            elif stmt.locks and not all(self.state.is_new(lock.schema, lock.name) for lock in stmt.locks):
                self.finding(
                    stmt,
                    "drop-index-not-concurrent",
                    "attention",
                    "Index dropped without CONCURRENTLY",
                    "DROP INDEX takes ACCESS EXCLUSIVE on the index's table, so it waits behind (and then "
                    "blocks) every running query on that table.",
                    "Use DROP INDEX CONCURRENTLY (outside a transaction block).",
                )
            return
        if remove in (ObjectType.OBJECT_TABLE, ObjectType.OBJECT_MATVIEW, ObjectType.OBJECT_VIEW):
            stmt.kind = {ObjectType.OBJECT_TABLE: "DROP TABLE", ObjectType.OBJECT_MATVIEW: "DROP MATERIALIZED VIEW"}.get(
                remove, "DROP VIEW"
            )
            stmt.effect = INSTANT
            for names in node.objects or ():
                parts = _strings(names)
                if parts:
                    stmt.locks.append(LockUse(parts[-2] if len(parts) > 1 else None, parts[-1], ACCESS_EXCLUSIVE))
            existing = [lock.label for lock in stmt.locks if not self.state.is_new(lock.schema, lock.name)]
            if remove == ObjectType.OBJECT_TABLE and existing:
                self.finding(
                    stmt,
                    "drop-table",
                    "attention",
                    f"Drops {', '.join(existing)}",
                    "The data is gone once this commits; code still reading the table fails immediately. "
                    "Dropping a partition this way also takes ACCESS EXCLUSIVE on the partitioned parent.",
                    "Make sure nothing uses it any more (and a backup exists); detach a partition first and "
                    "drop it afterwards.",
                )
            return
        stmt.kind = "DROP"

    def _TruncateStmt(self, stmt, node):
        stmt.kind = "TRUNCATE"
        stmt.effect = INSTANT
        for rangevar in node.relations or ():
            stmt.locks.append(LockUse(*_rel(rangevar), ACCESS_EXCLUSIVE))
        existing = [lock.label for lock in stmt.locks if not self.state.is_new(lock.schema, lock.name)]
        if existing:
            self.finding(
                stmt,
                "truncate",
                "attention",
                f"Truncates {', '.join(existing)}",
                "TRUNCATE removes every row at once under ACCESS EXCLUSIVE; it isn't MVCC-safe for "
                "concurrent transactions that already took a snapshot.",
                "Confirm the table really should be emptied in a schema migration; use DELETE in batches "
                "when only some rows should go.",
            )

    def _LockStmt(self, stmt, node):
        stmt.kind = "LOCK TABLE"
        mode = MODE_BY_NUMBER.get(node.mode or 8, ACCESS_EXCLUSIVE)
        for rangevar in node.relations or ():
            stmt.locks.append(LockUse(*_rel(rangevar), mode))

    def _VacuumStmt(self, stmt, node):
        options = _defelem_names(node.options)
        relations = [rel.relation for rel in node.rels or () if isinstance(rel, ast.VacuumRelation) and rel.relation]
        if node.is_vacuumcmd:
            self._require_no_transaction(stmt, "VACUUM")
        if node.is_vacuumcmd and "full" in options:
            stmt.kind = "VACUUM FULL"
            for rangevar in relations:
                stmt.locks.append(LockUse(*_rel(rangevar), ACCESS_EXCLUSIVE))
            stmt.effect = self._rewrite(stmt, "vacuum-full", "VACUUM FULL", ", ".join(r.relname for r in relations) or "every table", False)
            return
        stmt.kind = "VACUUM" if node.is_vacuumcmd else "ANALYZE"
        stmt.effect = SCAN
        for rangevar in relations:
            stmt.locks.append(LockUse(*_rel(rangevar), SHARE_UPDATE_EXCLUSIVE))

    def _ClusterStmt(self, stmt, node):
        stmt.kind = "CLUSTER"
        if node.relation is None:
            return
        stmt.locks.append(LockUse(*_rel(node.relation), ACCESS_EXCLUSIVE))
        stmt.effect = self._rewrite(stmt, "cluster", "CLUSTER", node.relation.relname, self.state.is_new(*_rel(node.relation)))

    def _ReindexStmt(self, stmt, node):
        concurrent = "concurrently" in _defelem_names(node.params)
        stmt.kind = "REINDEX CONCURRENTLY" if concurrent else "REINDEX"
        stmt.effect = INDEX_BUILD
        if concurrent:
            self._require_no_transaction(stmt, "REINDEX CONCURRENTLY")
        if node.relation is None:  # SCHEMA / DATABASE / SYSTEM
            return
        on_index = node.kind == ReindexObjectType.REINDEX_OBJECT_INDEX
        stmt.locks.append(LockUse(*_rel(node.relation), SHARE_UPDATE_EXCLUSIVE if concurrent else SHARE, via_index=on_index))
        if not concurrent:
            self.finding(
                stmt,
                "reindex-not-concurrent",
                "attention",
                "REINDEX without CONCURRENTLY",
                "REINDEX blocks writes to the table and takes ACCESS EXCLUSIVE on each index it rebuilds, so "
                "queries that would use those indexes wait too.",
                "Use REINDEX … CONCURRENTLY (PostgreSQL 12+, outside a transaction block).",
            )

    def _RefreshMatViewStmt(self, stmt, node):
        stmt.kind = "REFRESH MATERIALIZED VIEW"
        stmt.effect = REWRITE
        relation = _rel(node.relation)
        if node.concurrent:
            stmt.locks.append(LockUse(*relation, EXCLUSIVE))
            return
        stmt.locks.append(LockUse(*relation, ACCESS_EXCLUSIVE))
        if not self.state.is_new(*relation):
            self.finding(
                stmt,
                "refresh-matview-not-concurrent",
                "attention",
                "Materialized view refreshed without CONCURRENTLY",
                f"REFRESH takes ACCESS EXCLUSIVE on {node.relation.relname}, so nobody can read it until the "
                "refresh finishes.",
                "Use REFRESH MATERIALIZED VIEW CONCURRENTLY (needs a unique index on the view).",
            )

    # Data changes --------------------------------------------------------------

    def _UpdateStmt(self, stmt, node):
        self._data_change(stmt, node, "UPDATE")

    def _DeleteStmt(self, stmt, node):
        self._data_change(stmt, node, "DELETE")

    def _data_change(self, stmt, node, verb: str):
        stmt.kind = verb
        stmt.effect = ROWS
        relation = _rel(node.relation)
        stmt.locks.append(LockUse(*relation, ROW_EXCLUSIVE))
        if node.whereClause is None and not self.state.is_new(*relation):
            self.finding(
                stmt,
                "unbatched-backfill",
                "attention",
                f"{verb} of every row in {node.relation.relname} in one statement",
                "One statement over the whole table holds row locks on every row until it commits, writes "
                "a burst of WAL (replicas fall behind) and leaves as many dead rows for VACUUM.",
                "Backfill in batches (for example 1–10k rows by key range per transaction), committing "
                "between batches.",
            )

    def _DoStmt(self, stmt, node):
        stmt.kind = "DO"
        stmt.notes.append("A DO block runs procedural code: what it locks can't be told from the outside.")


def _binary_coercible(current: str, new_name: str, new_type) -> bool:
    """The common no-rewrite type changes: varchar(n) to a longer varchar,
    to unlimited varchar or to text; text to unlimited varchar; and
    numeric(p,s) to a larger precision with the same scale. `current` is
    format_type() output, e.g. 'character varying(50)'."""
    current = current.lower().strip()
    # Arrays (either side) aren't judged: assume the rewrite.
    if current.endswith("]") or (new_type is not None and new_type.arrayBounds):
        return False
    typmods = [_const_text(m) for m in (new_type.typmods or ())] if new_type is not None else []
    numbers = [int(t) if t is not None and t.isdigit() else None for t in typmods]
    current_numbers = [int(n) for n in re.findall(r"\d+", current[current.find("(") :])] if "(" in current else []
    if current.startswith("character varying"):
        old_len = current_numbers[0] if current_numbers else None
        if new_name == "text":
            return True
        if new_name == "varchar":
            new_len = numbers[0] if numbers else None
            return not typmods or (new_len is not None and old_len is not None and new_len >= old_len)
    if current == "text" and new_name == "varchar" and not typmods:
        return True
    if current.startswith("numeric(") and new_name == "numeric" and len(numbers) == 2 and len(current_numbers) == 2:
        old_precision, old_scale = current_numbers
        new_precision, new_scale = numbers
        return new_precision is not None and new_scale == old_scale and new_precision >= old_precision
    return False


# --- Live impact -------------------------------------------------------------------

SEVERITY_ORDER = {"healthy": 0, "info": 1, "attention": 2, "critical": 3}

BIG_TABLE_BYTES = 1024**3
MEDIUM_TABLE_BYTES = 100 * 1024**2
LONG_CONFLICT_SECONDS = 60
LONG_TRANSACTION_SECONDS = 300


def describe_impact(lock: LockUse, effect: str | None, facts: dict | None, oldest_xact_seconds: float | None) -> dict:
    """What taking `lock` would do on the live target right now.

    facts (routers/ddl_lint.py): {exists, table, kind, total_bytes,
    rows_estimate, holders: [{pid, mode, granted, application_name, state,
    xact_seconds}], scans_per_second, writes_per_second}."""
    if not facts or not facts.get("exists"):
        return {
            "relation": lock.label,
            "severity": "info",
            "text": f"{lock.label} doesn't exist on this target yet (created by this migration, or in another schema).",
            "conflicting_sessions": 0,
        }
    table = facts.get("table") or lock.label
    blocked = blocks(lock.mode)
    conflicting = [h for h in facts.get("holders", []) if h["mode"] in LOCK_STRENGTH and conflicts(lock.mode, h["mode"])]
    oldest = max(conflicting, key=lambda h: h.get("xact_seconds") or 0, default=None)
    oldest_seconds = (oldest or {}).get("xact_seconds") or 0
    size = facts.get("total_bytes")
    slow = effect in SLOW_EFFECTS

    parts = [f"Takes {lock.mode} on {table} ({format_bytes(size)}"]
    if facts.get("rows_estimate"):
        parts[0] += f", ~{facts['rows_estimate']:,} rows"
    parts[0] += ")"
    if blocked != "nothing":
        parts[0] += f", blocking {blocked} on it"
        if effect == INSTANT:
            parts[0] += " — briefly, once granted"
        elif slow:
            parts[0] += " for the whole " + {
                ROWS: "update",
                SCAN: "scan",
                INDEX_BUILD: "index build",
                REWRITE: "rewrite",
            }[effect]
    parts[0] += "."
    scans, writes = facts.get("scans_per_second"), facts.get("writes_per_second")
    if scans is not None or writes is not None:
        parts.append(
            f"Over the last hour about {scans or 0:.1f} scans/s and {writes or 0:.1f} row changes/s hit this table"
            + (" — all of them would wait." if blocked == "reads and writes" else ".")
        )
    if conflicting:
        holder = oldest or conflicting[0]
        app = holder.get("application_name") or "unnamed client"
        parts.append(
            f"{len(conflicting)} session(s) hold conflicting locks on it right now; the oldest has been in its "
            f"transaction for {format_seconds(oldest_seconds)} (pid {holder['pid']}, {app}, {holder.get('state') or 'unknown state'}). "
            "The statement would queue behind it"
            + (", and every query on the table that arrives later would queue behind the statement." if blocked != "nothing" else ".")
        )
    if lock.mode == SHARE_UPDATE_EXCLUSIVE and effect == INDEX_BUILD and (oldest_xact_seconds or 0) >= LONG_TRANSACTION_SECONDS:
        parts.append(
            f"The longest open transaction in this database is {format_seconds(oldest_xact_seconds)} old; a "
            "concurrent build waits for older transactions before it can finish."
        )

    blocks_traffic = blocked != "nothing"
    waits_for_old_transaction = effect == INDEX_BUILD and (oldest_xact_seconds or 0) >= LONG_TRANSACTION_SECONDS
    if blocks_traffic and (oldest_seconds >= LONG_CONFLICT_SECONDS or (slow and (size or 0) >= BIG_TABLE_BYTES)):
        severity = "critical"
    elif (blocks_traffic and (conflicting or (slow and (size or 0) >= MEDIUM_TABLE_BYTES) or (scans or 0) >= 1)) or (
        waits_for_old_transaction
    ):
        severity = "attention"
    else:
        severity = "healthy"
    return {
        "relation": table,
        "severity": severity,
        "text": " ".join(parts),
        "conflicting_sessions": len(conflicting),
        "oldest_conflict_seconds": oldest_seconds if conflicting else None,
        "total_bytes": size,
        "scans_per_second": scans,
        "writes_per_second": writes,
    }


def lint(sql: str, catalog: Catalog | None = None) -> LintResult:
    """Splits and parses `sql` with PostgreSQL's own grammar and analyses
    each statement in order. A syntax error stops the analysis and is
    returned with its line and column."""
    catalog = catalog or _NoCatalog()
    if len(sql) > MAX_SQL_CHARS:
        return LintResult([], [], {"message": f"The migration is longer than {MAX_SQL_CHARS:,} characters.", "line": 1, "column": 1})
    try:
        slices = split(sql, with_parser=True, only_slices=True)
    except ParseError as exc:
        index = exc.args[1] if len(exc.args) > 1 and exc.args[1] is not None else 0
        line, column = _line_of(sql, index)
        return LintResult([], [], {"message": str(exc.args[0]), "line": line, "column": column})
    if len(slices) > MAX_STATEMENTS:
        return LintResult([], [], {"message": f"More than {MAX_STATEMENTS} statements.", "line": 1, "column": 1})

    linter = _Linter(catalog)
    statements = []
    for number, piece in enumerate(slices, start=1):
        text = sql[piece]
        start = piece.start + _first_token(text)
        line, _column = _line_of(sql, start)
        stmt = StatementResult(index=number, line=line, sql=" ".join(sql[start : piece.stop].split()), kind="")
        node = parse_sql(text)[0].stmt
        stmt.kind = type(node).__name__.removesuffix("Stmt").upper()
        linter.run(stmt, node)
        statements.append(stmt)
    return LintResult(statements, linter.findings)
