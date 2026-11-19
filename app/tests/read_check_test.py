"""The read check before a job takes a table: discovery asks SQL Server whether the connection's login can read each
table it lists (source_catalog.CAN_READ_SQL), Discover greys out what it can't read, and Create, Add tables and a
re-discovery refuse such a table, named with its database and why (new_job.UNREADABLE_TABLE).

The case behind it: a login with VIEW DEFINITION on the server and no SELECT in one database lists all of that
database's tables, and a job made from them failed on every one with "The SELECT permission was denied on the object
..., database 'lfc_test'". Plain functions and the Create endpoint with a fake database; no SQL Server, no Lakebase."""
from pathlib import Path

import pytest

import new_job as nj
import queries as q
import source_catalog as catalog
import warehouse_reads as wr
from new_job_test import FAN_IN, client  # noqa: F401  (the Create endpoint with a fake database)

FRONTEND = Path(__file__).resolve().parents[1] / "src" / "frontend" / "src"


def _discovered(database: str, schema: str, table: str, can_read) -> dict:
    """A discovered table's fields, as source_rows_sql('discovery') builds them for a selection."""
    return {"src_connection": "lfc_sqlserver_discover", "src_database": database, "src_schema": schema,
            "src_table": table, "select_list": "id, name", "primary_key": "id", "load_type": "ct",
            "can_read": can_read}


def _selection_text(*fields: dict) -> str:
    """The CSV a selection by discovery hands to the plan (main._selected): DISCOVERY_COLUMNS of each table's fields."""
    return nj.csv_of(list(nj.DISCOVERY_COLUMNS), list(fields))


# ---- the probe ----------------------------------------------------------------------------------

def test_the_probe_asks_for_select_once_per_table_and_by_column_only_where_columns_have_their_own_permissions():
    probe = catalog.CAN_READ_SQL
    table = "QUOTENAME(s.name) + '.' + QUOTENAME(t.name)"
    # One check per table: SELECT on it, with the login's roles, grants and denies.
    assert f"ELSE ISNULL(HAS_PERMS_BY_NAME({table}, 'OBJECT', 'SELECT'), 0) END" in probe
    # A table with column-level grants or denies is checked on every column of its select list, the columns discovery
    # doesn't leave out, as Spark JDBC's schema step and the read both need.
    assert "p.class = 1 AND p.major_id = t.object_id AND p.minor_id > 0" in probe
    assert f"HAS_PERMS_BY_NAME({table}, 'OBJECT', 'SELECT', c.name, 'COLUMN'), 0) = 0" in probe
    assert f"NOT {catalog.UNREADABLE_COLUMN_SQL}" in probe
    # A NULL answer (a name it can't resolve) counts as not readable, in both branches.
    assert probe.count("ISNULL(HAS_PERMS_BY_NAME(") == 2
    assert f"{probe} AS can_read" in catalog.TABLE_SUMMARY_SQL
    # Parentheses balance, so the expression drops into the summary and the database check whole.
    assert probe.count("(") == probe.count(")")


def test_the_app_and_the_engine_ask_the_same_question():
    import source_catalog_test
    discovery = source_catalog_test._engine()
    assert discovery.CAN_READ_SQL == catalog.CAN_READ_SQL
    assert discovery.UNREADABLE_COLUMN_SQL == catalog.UNREADABLE_COLUMN_SQL
    assert f"{discovery.CAN_READ_SQL} AS can_read" in discovery.TABLES_SQL


@pytest.mark.parametrize("given, kept", [(1, True), (0, False), ("1", True), ("0", False), (None, None)])
def test_a_tables_answer_is_kept_as_true_false_or_unknown(given, kept):
    # A SQL warehouse gives every value as text; a read without the column says nothing.
    row = {"sch": "dbo", "tbl": "customers", "row_count": "5", "ct_enabled": "1", "can_read": given,
           "select_cols": "id, name", "pk_cols": "id", "pk_unreadable": "0", "pk_first_type": "int"}
    [table] = catalog.assemble_summary([row])
    assert table["can_read"] is kept
    assert dict(zip(wr.TABLE_COLUMNS, wr.table_rows("d1", "lfc_test", [row])[0]))["can_read"] is kept


# ---- the database check (Discover's database list) ----------------------------------------------

class _Checks:
    def __init__(self):
        self.done = []

    def finish_check(self, discovery_id, database, ok, tables, why):
        self.done.append((database, ok, tables, why))


@pytest.mark.parametrize("rows, open_", [([{"tables": "1002", "readable": "1"}], True),
                                         ([{"tables": "0", "readable": "0"}], True),
                                         ([{"tables": "12"}], True)])
def test_a_database_the_login_can_read_or_with_no_tables_stays_open(rows, open_):
    store = _Checks()
    wr.finish(store, wr.CHECK, "d1", "lfc_eng_001", "s1", {"state": "SUCCEEDED", "rows": rows})
    [(_, ok, _, why)] = store.done
    assert ok is open_ and why is None


def test_a_database_whose_tables_the_login_can_list_but_not_read_is_closed_with_why():
    store = _Checks()
    wr.finish(store, wr.CHECK, "d1", "lfc_test", "s1",
              {"state": "SUCCEEDED", "rows": [{"tables": "1002", "readable": "0"}]})
    [(database, ok, tables, why)] = store.done
    assert (database, ok, tables) == ("lfc_test", False, 1002)
    assert why.startswith("the connection's login can open this database but can't read any of its 1,002 tables")
    assert "db_datareader" in why


def test_the_database_check_counts_its_tables_and_stops_at_the_first_readable_one():
    assert wr.ACCESS_SQL.startswith("SELECT COUNT(*) AS tables, CAST(CASE WHEN EXISTS (")
    assert f"{catalog.CAN_READ_SQL} = 1" in wr.ACCESS_SQL and "AS readable" in wr.ACCESS_SQL


# ---- the refusal ---------------------------------------------------------------------------------

def test_a_selection_carries_each_tables_answer_to_the_plan():
    discovered = q.source_rows_sql("discovery")
    assert "'can_read', t.can_read" in discovered and "t.can_read, 0 as line" in discovered
    assert "null::boolean as can_read" in q.source_rows_sql("upload")
    assert "can_read" in nj.DISCOVERY_COLUMNS


def test_a_table_the_catalog_lists_but_the_login_cant_read_is_an_invalid_line_named_with_its_database():
    text = _selection_text(_discovered("lfc_test", "dbo", "customers", False),
                           _discovered("lfc_test", "dbo", "orders", None),       # discovered before the check
                           _discovered("lfc_eng_001", "dbo", "customers", True))
    plan = nj.plan("Job 52", text, FAN_IN, 1)
    assert [(line["table"], line["status"]) for line in plan["lines"]] == [
        ("lfc_test.dbo.customers", "invalid"), ("lfc_test.dbo.orders", "valid"), ("lfc_eng_001.dbo.customers", "valid")]
    assert plan["lines"][0]["errors"] == [nj.UNREADABLE_TABLE]
    assert "can_read" not in plan["lines"][1]["row"]          # the answer is not a control-row column
    assert nj.summary(plan)["invalid"] == [{"line": 2, "table": "lfc_test.dbo.customers",
                                            "errors": [nj.UNREADABLE_TABLE]}]


def test_the_refusal_says_why_and_what_to_do():
    assert "can't read this table" in nj.UNREADABLE_TABLE and "SELECT" in nj.UNREADABLE_TABLE
    assert "leave it out" in nj.UNREADABLE_TABLE and "read its database again" in nj.UNREADABLE_TABLE


def test_create_refuses_a_job_with_a_table_the_login_cant_read_and_writes_nothing(client):  # noqa: F811
    text = _selection_text(*[_discovered("lfc_test", "gen", f"t{n:04d}", False) for n in range(1, 4)],
                           _discovered("lfc_eng_001", "dbo", "customers", True))
    preview = client.post("/api/ingestion-jobs", json={"name": "Job 52", "csv": text, "settings": FAN_IN}).json()
    named = [(line["table"], line["errors"]) for line in preview["summary"]["invalid"]]
    assert named == [(f"lfc_test.gen.t{n:04d}", [nj.UNREADABLE_TABLE]) for n in range(1, 4)]
    created = client.post("/api/ingestion-jobs", json={"name": "Job 52", "csv": text, "settings": FAN_IN,
                                                       "dry_run": False}).json()
    assert created["created"] is False and len(created["summary"]["invalid"]) == 3
    assert client.db.statements == [] and client.synced == []


def test_adding_tables_to_a_job_refuses_an_unreadable_one_too():
    from new_job_test import _job_rows
    text = _selection_text(_discovered("lfc_test", "dbo", "customers", False),
                           _discovered("lfc_eng_001", "dbo", "customers", True))
    plan = nj.plan_for_job("Engagement extraction", text, "ct", _job_rows(), 4)
    assert plan["errors"] == []
    assert [(line["table"], line["status"]) for line in plan["lines"]] == [
        ("lfc_test.dbo.customers", "invalid"), ("lfc_eng_001.dbo.customers", "valid")]
    assert plan["lines"][0]["errors"] == [nj.UNREADABLE_TABLE]


@pytest.mark.parametrize("value, refused", [("False", True), ("false", True), ("0", True), ("True", False),
                                            ("", False), (None, False)])
def test_only_a_no_from_discovery_refuses_a_line(value, refused):
    assert nj.unreadable(value) is refused


def test_a_blank_or_empty_line_shifts_no_tables_answer_onto_another_table():
    text = ("src_connection,src_database,src_schema,src_table,select_list,primary_key,load_type,can_read\n"
            "c,lfc_test,dbo,a,id,id,full,False\n"
            "\n,,,,,,,\n"
            "c,lfc_test,dbo,b,id,id,,True\n")
    lines = nj.plan("Job", text, FAN_IN, 1)["lines"]
    assert [(line["row"]["src_table"], line["status"], line["row"]["load_type"]) for line in lines] == [
        ("a", "invalid", "full"), ("b", "valid", "ct")]


def test_a_database_set_aside_while_preparing_says_how_many_tables_cant_be_taken():
    import main
    invalid = [{"table": f"lfc_test.gen.t{n:04d}", "errors": [nj.UNREADABLE_TABLE]} for n in range(1, 1003)]
    why = main._invalid_why(invalid)
    assert why.startswith(f"lfc_test.gen.t0001: {nj.UNREADABLE_TABLE}") and why.endswith("(and 1,001 more tables)")
    assert main._invalid_why(invalid[:1]) == f"lfc_test.gen.t0001: {nj.UNREADABLE_TABLE}"


# ---- what Discover shows -------------------------------------------------------------------------

def test_discovers_table_list_greys_out_and_says_which_tables_cant_be_read():
    pick = (FRONTEND / "pages" / "SourcePick.tsx").read_text()
    assert 'const noRead = t.can_read === false;' in pick
    assert 'noRead && !on ? " closed" : ""' in pick and "(!noRead || on) && onToggle(key)" in pick
    assert "<Tip text={UNREADABLE_TIP} focusable><span className=\"tag bad\">Can't read</span></Tip>" in pick
    assert "so Create refuses" in pick and "{fmtInt(d.unreadable)} can't be read" in pick
    assert "unreadable" in q.source_tables_sql("discovery") and "unreadable" in q.source_databases_sql("discovery")


def test_bulk_import_starts_a_table_the_login_cant_read_unticked():
    discovered = [{"src_database": "lfc_test", "src_schema": "dbo", "src_table": name, "select_list": "id",
                   "primary_key": "id", "load_type": "ct", "row_count": 1, "size_mb": 1, "can_read": can_read,
                   "src_connection": "c", "src_type": "sqlserver"}
                  for name, can_read in (("customers", False), ("orders", True), ("lines", None))]
    rows = q.review_rows(discovered, {"sink_catalog": "main", "sink_schema": "s", "sink_table": "{table}"})
    assert [(row["src_table"], row["include"]) for row in rows] == [
        ("customers", False), ("orders", True), ("lines", True)]


def test_a_database_read_again_replaces_what_its_first_read_found():
    insert = q.insert_discovered_tables_sql(wr.TABLE_COLUMNS, 1)
    assert "on conflict (discovery_id, src_database, src_schema, src_table) do update set " in insert
    assert "can_read = excluded.can_read" in insert and "src_table = excluded" not in insert


def test_a_database_being_prepared_with_unreadable_tables_is_failed_so_read_again_reads_it(monkeypatch):
    import main
    from by_database import Unit
    calls = []
    monkeypatch.setattr(main, "_job_discovery_ids", lambda job: ["d1"])
    monkeypatch.setattr(main.db, "query", lambda sql, params=(): calls.append((sql, params)) or [{"src_database": "x"}])
    unreadable = [{"table": "lfc_test.dbo.customers", "errors": [nj.UNREADABLE_TABLE]}]
    assert main._unreadable_read({"id": 52}, Unit("lfc_sqlserver_discover", "LFC_test", True), unreadable)
    [(sql, params)] = calls
    assert sql == q.prepare_unreadable_read_sql()
    assert params == (main._invalid_why(unreadable), ["d1"], "52", "lfc_sqlserver_discover", "lfc_test")
    # A clash of another kind stays a clash: nothing is failed.
    clash = [{"table": "lfc_eng_001.dbo.customers", "errors": ["line 3 already writes main.s.customers"]}]
    assert not main._unreadable_read({"id": 52}, Unit("lfc_sqlserver_discover", "lfc_eng_001", True), clash)
    assert len(calls) == 1
    failed = q.prepare_unreadable_read_sql()
    assert failed.startswith("update lakefed_ingest.discovery set status = 'failed', error = %s")
    assert "status = 'ok'" in failed and "returning src_database" in failed
