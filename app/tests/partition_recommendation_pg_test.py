"""A discovered table read by queries.source_rows_sql gets the partitioning Bulk import's review recommends once
new_job plans it, against a real Postgres: the rows carry each table's size and partition candidate, and
queries.recommended_partitioning (the one place the rule is kept) partitions a table of at least
DEFAULT_PARTITION_THRESHOLD_MB with a candidate on it, and no other. Skipped unless LAKEFED_APP_TEST_PG names a
Postgres server (see monitoring_pg_test.py)."""
import os
import sys
import uuid
from pathlib import Path

import new_job as nj
import pytest
import queries as q

URI = os.environ.get("LAKEFED_APP_TEST_PG")
pytestmark = pytest.mark.skipif(not URI, reason="LAKEFED_APP_TEST_PG not set")
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pg():
    psycopg = pytest.importorskip("psycopg")
    sys.path.insert(0, str(REPO / "src" / "lakefed_ingest"))
    import state_store
    name = f"lakefed_app_test_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(URI, autocommit=True)
    admin.execute(f"create database {name}")
    conn = psycopg.connect(psycopg.conninfo.make_conninfo(URI, dbname=name), autocommit=True)
    try:
        for statement in state_store.split_sql_statements((REPO / "src/lakefed_ingest/lakebase_schema.sql").read_text()):
            conn.execute(statement)
        conn.execute("insert into lakefed_ingest.discovery (discovery_id, src_type, src_connection, src_database, status, "
                     "finished_at) values ('d1', 'sqlserver', 'sql_a', 'sales', 'ok', now())")
        big, small = q.DEFAULT_PARTITION_THRESHOLD_MB, q.DEFAULT_PARTITION_THRESHOLD_MB - 1
        for table, size, candidate in (("big", big, "order_id"), ("big_no_column", big * 3, None),
                                       ("small", small, "id"), ("unknown_size", None, "id")):
            conn.execute("insert into lakefed_ingest.discovered_table (discovery_id, src_database, src_schema, src_table, "
                         "size_mb, partition_candidate, select_list, primary_key, load_type) "
                         "values ('d1', 'sales', 'dbo', %s, %s, %s, 'id', 'id', 'full')", (table, size, candidate))
        yield conn
    finally:
        conn.close()
        admin.execute(f"drop database {name}")
        admin.close()


def test_a_large_discovered_table_with_a_partition_column_is_planned_partitioned(pg):
    from psycopg.rows import dict_row
    with pg.cursor(row_factory=dict_row) as cur:
        cur.execute(q.source_rows_sql("discovery"), (["d1"],))
        rows = cur.fetchall()
    found = {r["src_table"]: r["fields"] for r in rows}
    assert found["big"]["partition_candidate"] == "order_id" and float(found["big"]["size_mb"]) == q.DEFAULT_PARTITION_THRESHOLD_MB
    assert not any(c in f for f in found.values() for c in nj.PARTITION_COLUMNS)   # the rule is applied in Python only
    plan = nj.plan("Sales", nj.csv_of(list(nj.DISCOVERY_COLUMNS), [r["fields"] for r in rows]),
                   {"sink_catalog": "main", "sink_schema": "s", "sink_table": "{database}_{table}"}, 1)
    assert plan["errors"] == []
    partitioning = {line["row"]["src_table"]: (line["row"].get("load_partitioned"), line["row"].get("partition_col"),
                                               line["row"].get("partition_size_mb")) for line in plan["lines"]}
    assert partitioning == {"big": (True, "order_id", q.DEFAULT_PARTITION_SIZE_MB),
                            "big_no_column": (None, None, None), "small": (None, None, None),
                            "unknown_size": (None, None, None)}
