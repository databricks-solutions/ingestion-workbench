"""The warning for a shared target whose tables load in different ways, against a real Postgres: the one index probe per
target (queries.mixed_load_sinks_sql and mixed_load_sinks_of_groups_sql), and the writes that come back with it. The
job's rows are job_settings_pg_test.py's: two groups of two tables on the shared targets bronze.extraction.orderline
and bronze.extraction.invoice, append mode, change tracked, and one more OrderLine row of another group on the first.

Skipped unless LAKEFED_APP_TEST_PG names a Postgres server (see monitoring_pg_test.py)."""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from job_settings_pg_test import client  # noqa: E402,F401  (a Postgres of its own, the job's rows, the app pointed at it)

import main  # noqa: E402

URI = os.environ.get("LAKEFED_APP_TEST_PG")
pytestmark = pytest.mark.skipif(not URI, reason="LAKEFED_APP_TEST_PG not set")
ORDER_LINE = "bronze.extraction.orderline"


def test_a_table_edit_to_full_reload_on_a_target_of_change_tracked_tables_is_applied_with_a_warning(client):
    r = _edit_table(client, "OrderLine", {"load_type": "full"})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["applied"] is True
    [warning] = r.json()["warnings"]
    assert warning["kind"] == "mixed_load" and warning["count"] == 1 and warning["append_count"] == 1
    assert warning["targets"] == [{"target": ORDER_LINE, "full": 2, "other": 1, "append": True}]
    assert warning["message"].startswith(f"Target {ORDER_LINE} is shared by tables with different load types: "
                                         "2 full reload and 1 change-tracked or cursor.")
    assert "appends all of its rows to the target again" in warning["message"]
    assert _load_types(client, "orderline") == ["ct", "full", "full"]


def test_a_dry_run_of_that_edit_writes_nothing_and_warns_of_nothing(client):
    r = client.post(f"/api/ingestion-jobs/{client.sid}/tables/edit",
                    json={"schema": "dbo", "table": "OrderLine", "changes": {"load_type": "full"}})
    assert r.status_code == 200 and "warnings" not in r.json()
    assert _load_types(client, "orderline") == ["ct", "ct", "ct"]


def test_an_edit_of_a_target_that_was_mixed_before_it_is_no_warning(client):
    assert _edit_table(client, "OrderLine", {"load_type": "full"}).json()["warnings"]
    again = _edit_table(client, "OrderLine", {"select_list": "id,qty,note"})
    assert again.status_code == 200 and again.json()["summary"]["applied"] is True
    assert again.json()["warnings"] == []


def test_an_edit_that_leaves_every_table_of_the_target_a_full_reload_is_no_warning(client):
    _set(client, "update lakefed_ingest.control set load_type = 'full' where src_database = 'X'")
    r = _edit_table(client, "OrderLine", {"load_type": "full"})
    assert r.status_code == 200 and r.json()["summary"]["applied"] is True and r.json()["warnings"] == []


def test_a_job_edit_of_its_tables_load_type_on_a_dedicated_target_asks_nothing(client):
    _set(client, "update lakefed_ingest.control set client_id = null, sink_table = lower(src_database || src_table), "
                 "client_attrs = null")
    r = _edit_table(client, "OrderLine", {"load_type": "full"})
    assert r.status_code == 200 and r.json()["summary"]["applied"] is True and r.json()["warnings"] == []


def test_a_change_tracked_table_with_a_cursor_table_on_a_target_is_no_mix(client):
    _set(client, "update lakefed_ingest.control set load_type = 'cursor', cursor_column = 'modified', "
                 "write_mode = 'merge' where src_database = 'db_1' and src_table = 'OrderLine'")
    assert _targets(client) == []


def test_every_load_type_besides_full_reload_mixes_with_it_on_a_target(client):
    _set(client, "update lakefed_ingest.control set load_type = 'full' where src_database = 'db_1' and "
                 "src_table = 'OrderLine'")
    assert [t["target"] for t in _targets(client)] == [ORDER_LINE]
    _set(client, "update lakefed_ingest.control set load_type = 'cursor', cursor_column = 'modified', "
                 "write_mode = 'merge' where src_table = 'OrderLine' and load_type = 'ct'")
    assert [t["target"] for t in _targets(client)] == [ORDER_LINE]


def test_the_counts_are_per_target_and_the_append_flag_follows_a_full_reload_table(client):
    _set(client, "update lakefed_ingest.control set load_type = 'full', write_mode = 'merge' "
                 "where src_database = 'db_1'")
    _set(client, "update lakefed_ingest.control set write_mode = 'merge' where src_database <> 'db_1' "
                 "and src_table = 'Invoice'")
    assert _targets(client) == [{"target": "bronze.extraction.invoice", "full": 1, "other": 1, "append": False},
                                {"target": ORDER_LINE, "full": 1, "other": 2, "append": False}]
    _set(client, "update lakefed_ingest.control set write_mode = 'append' where src_database = 'db_1' "
                 "and src_table = 'OrderLine'")
    assert [t["append"] for t in _targets(client)] == [False, True]


def test_a_disabled_full_reload_table_still_counts_and_names_are_compared_without_case(client):
    _set(client, "update lakefed_ingest.control set load_type = 'full', task_enabled = false "
                 "where src_database = 'db_1' and src_table = 'OrderLine'")
    _set(client, "update lakefed_ingest.control set sink_table = 'OrderLine' where src_table = 'OrderLine' "
                 "and src_database <> 'db_1'")
    [found] = _targets(client)
    assert found == {"target": ORDER_LINE, "full": 1, "other": 2, "append": True}


def test_the_check_finds_targets_by_name_among_those_the_rows_write_and_ignores_rows_with_no_client(client):
    rows = [{"sink_catalog": "bronze", "sink_schema": "extraction", "sink_table": "OrderLine", "client_id": "x"}]
    _set(client, "update lakefed_ingest.control set load_type = 'full' where src_database = 'db_1' and "
                 "src_table = 'OrderLine'")
    assert [t["target"] for t in _found(rows)] == [ORDER_LINE]
    assert _found([dict(rows[0], client_id=None)]) == []
    assert _found([dict(rows[0], sink_table="Invoice")]) == []


def test_the_whole_job_edit_that_moves_a_table_onto_a_full_reload_target_warns(client):
    _set(client, "insert into lakefed_ingest.control (task_collection, src_type, src_connection, use_remote_query, "
                 "src_database, src_schema, src_table, select_list, primary_key, load_type, write_mode, "
                 "sink_catalog, sink_schema, sink_table, client_id) values ('other_group', 'sqlserver', 'sql_01', "
                 "true, 'Y', 'dbo', 'OrderLine', 'id', 'id', 'full', 'append', 'bronze', 'current', 'orderline', 'Y')")
    r = client.post(f"/api/ingestion-jobs/{client.sid}/settings",
                    json={"settings": {"write_mode_changes": "merge", "sink_schema": "current"}, "dry_run": False})
    assert r.status_code == 200, r.text
    [warning] = r.json()["warnings"]
    assert warning["kind"] == "mixed_load"
    assert warning["targets"] == [{"target": "bronze.current.orderline", "full": 1, "other": 2, "append": True}]
    assert warning["count"] == 1


def test_the_whole_job_edit_of_a_job_already_on_a_mixed_target_is_no_warning(client):
    _set(client, "update lakefed_ingest.control set load_type = 'full' where src_database = 'X'")
    r = client.post(f"/api/ingestion-jobs/{client.sid}/settings",
                    json={"settings": {"freshness_sla_minutes": 90}, "dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["applied"] is True and r.json()["warnings"] == []


def _edit_table(client, table, changes):
    return client.post(f"/api/ingestion-jobs/{client.sid}/tables/edit",
                       json={"schema": "dbo", "table": table, "changes": changes, "dry_run": False})


def _set(client, statement):
    import psycopg
    with psycopg.connect(client.target, autocommit=True) as conn:
        conn.execute(statement)


def _load_types(client, sink_table):
    import psycopg
    with psycopg.connect(client.target) as conn:
        return sorted(load_type for (load_type,) in conn.execute(
            "select load_type from lakefed_ingest.control where lower(sink_table) = %s", (sink_table,)).fetchall())


def _targets(client):
    """Every mixed shared target of the job's tables, as the app finds them after a write."""
    conn = main.db.connect()
    try:
        return main._mixed_load_targets_of_groups(conn.cursor(), ["cust_c01", "cust_c02", "other_group"])
    finally:
        conn.close()


def _found(rows):
    conn = main.db.connect()
    try:
        return main._mixed_load_targets(conn.cursor(), rows)
    finally:
        conn.close()
