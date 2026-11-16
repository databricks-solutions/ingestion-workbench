"""The shared-database rule against a real Postgres: a database may be in a second live job only when the two jobs
write to different catalog.schema targets (app/src/shared_databases.py).

The first part runs the rule's reads and the write's final check (main._refuse_shared_targets) as the tables' owner;
the second drives Create and Restore through the API as the app's own role (app_role_pg_test's client).

Skipped unless LAKEFED_APP_TEST_PG names a Postgres server where the test may create and drop its own database."""
import os

import pytest

pytest.importorskip("fastapi")
from app_role_pg_test import SETTINGS, _discover, client  # noqa: E402,F401  (client is the app-role fixture)
from delete_job_pg_test import _job, pg  # noqa: E402,F401  (pg is the owner's fixture)

import main  # noqa: E402
import queries as q  # noqa: E402
import shared_databases as sb  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("LAKEFED_APP_TEST_PG"), reason="LAKEFED_APP_TEST_PG not set")


@pytest.fixture(autouse=True)
def no_connections(monkeypatch):
    """The workspace's connections, as the rule reads them: none here, unless a test says otherwise."""
    monkeypatch.setattr(main, "_connection_endpoints", lambda wanted=(): {})


# ---- the reads and the write's final check, as the owner ------------------------------------------------------------

def test_a_second_job_may_read_a_database_into_another_schema_and_is_refused_into_the_same_one(pg):
    first = _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    _job(pg, "Second", ["b1"])
    _check(pg, [_add(pg, "b1", "db", "copy")])                       # another schema: no refusal
    with pytest.raises(main.HTTPException) as refused:
        _check(pg, [_add(pg, "b1", "db", "sales", table="u")])
    assert refused.value.status_code == 409
    assert refused.value.detail["message"].startswith(
        "database db is also in the ingestion job First, which writes to m.sales too")
    assert refused.value.detail["jobs"] == [{"id": first, "name": "First"}]


def test_a_new_target_the_other_job_writes_is_refused_when_the_jobs_share_a_database(pg):
    _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    _job(pg, "Second", ["b1"])
    _check(pg, [_add(pg, "b1", "db", "copy")])
    with pytest.raises(main.HTTPException) as refused:              # another database, into First's schema
        _check(pg, [_add(pg, "b1", "other_db", "sales")])
    assert "database db is also in the ingestion job First, which writes to m.sales too" in refused.value.detail[
        "message"]


def test_the_same_schema_without_a_shared_database_is_no_concern_of_the_rule(pg):
    _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    _job(pg, "Second", ["b1"])
    _check(pg, [_add(pg, "b1", "other_db", "sales")])               # fan-in from other databases stays allowed


def test_a_clash_two_jobs_had_before_the_rule_is_not_refused_again_by_a_write_that_adds_nothing_new(pg):
    _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    _job(pg, "Second", ["b1"])
    _add(pg, "b1", "db", "sales")                                    # written before the rule (a split was a warning)
    _check(pg, [_add(pg, "b1", "third_db", "elsewhere")])           # touches neither the database nor the schema
    _check(pg, [_add(pg, "b1", "db", "sales", table="u")])           # the job had both already
    _add(pg, "a1", "db2", "first_only")
    with pytest.raises(main.HTTPException):                          # a database new to Second, that First reads
        _check(pg, [_add(pg, "b1", "db2", "sales")])


def test_a_deleted_job_a_removed_member_and_a_group_both_jobs_hold_are_not_another_job_reading(pg):
    _job(pg, "Gone", ["a1"], deleted=True)
    _add(pg, "a1", "db", "sales")
    other = _job(pg, "Other", ["a2"])
    pg.execute("update lakefed_ingest.schedule_member set removed_at = now() where schedule_id = %s", (other,))
    _add(pg, "a2", "db", "sales")
    _job(pg, "One", ["shared"])
    _job(pg, "Two", ["shared"])
    _check(pg, [_add(pg, "shared", "db", "sales")])                  # one group's read, held by two jobs


def test_a_write_of_every_row_checks_them_all(pg):
    _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    _job(pg, "Second", ["b1"])
    _add(pg, "b1", "db", "copy")
    main._refuse_shared_targets(pg.cursor(), ["b1"], confirm_shared=True)    # a Restore or a settings edit
    pg.execute("update lakefed_ingest.control set sink_schema = 'Sales' where task_collection = 'b1'")
    with pytest.raises(main.HTTPException) as refused:
        main._refuse_shared_targets(pg.cursor(), ["b1"], confirm_shared=True)
    assert "which writes to m.sales too" in refused.value.detail["message"]   # compared without case


def test_the_same_server_and_name_through_another_login_is_the_same_database(pg):
    _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    _job(pg, "Second", ["b1"])
    with pytest.raises(main.HTTPException):                          # the login is not part of the server
        _check(pg, [_add(pg, "b1", "db", "sales", secret="k2")])


def test_the_live_owners_of_some_databases_and_their_jobs_targets(pg):
    first = _job(pg, "First", ["a1", "a2"])
    _add(pg, "a1", "db", "sales")
    _add(pg, "a2", "db2", "Hr", catalog="M")
    _job(pg, "Gone", ["g1"], deleted=True)
    _add(pg, "g1", "db", "old")
    keys = [sb.server_key(_source(name), {}) for name in ("db", "db2", "none")]
    found = pg.execute(q.live_database_owners_sql(), {"keys": keys, "ep_names": [], "ep_servers": []}).fetchall()
    assert sorted((r[1], r[2], r[3]) for r in found) == [("db", first, "First"), ("db2", first, "First")]
    asked = (["m", "m", "m"], ["hr", "sales", "none"], [first])
    assert pg.execute(q.jobs_writing_targets_sql(), asked).fetchall() == [(first, "m.hr"), (first, "m.sales")]


def test_a_group_made_a_jobs_member_brings_its_database_and_its_schema_along(pg):
    _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    _add(pg, "loose", "db", "sales")                                 # a group no job holds yet
    _job(pg, "Second", ["loose"])                                    # an edit's new member
    with pytest.raises(main.HTTPException):
        main._refuse_shared_targets(pg.cursor(), ["loose"], confirm_shared=True)


def test_a_group_both_jobs_hold_gives_them_no_common_target(pg):
    _job(pg, "Mine", ["both", "m1"])
    _job(pg, "Theirs", ["both", "t1"])
    _add(pg, "both", "db0", "common")
    _add(pg, "t1", "db", "theirs")
    _check(pg, [_add(pg, "m1", "db", "mine")])                       # common is one read through one group


def test_planned_rows_are_checked_before_any_is_written(pg, monkeypatch):
    def query(sql, params=()):
        cursor = pg.execute(sql, params)
        return [dict(zip([d.name for d in cursor.description], row)) for row in cursor.fetchall()]

    monkeypatch.setattr(main.db, "query", query)                     # the app's reads, on this test's database
    _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    second = _job(pg, "Second", ["b1"])
    planned = main._shape_of([{**_source("db"), "sink_catalog": "m", "sink_schema": "copy"},
                              {**_source("other_db"), "sink_catalog": "m", "sink_schema": "sales"}])
    with pytest.raises(main.HTTPException) as refused:
        main._refuse_shared_before(second, planned, confirm_shared=True)     # db shared, sales planned for other_db
    assert "which writes to m.sales too" in refused.value.detail["message"]
    main._refuse_shared_before(second, main._shape_of([{**_source("db"), "sink_catalog": "m", "sink_schema": "copy"}]),
                               confirm_shared=True)
    assert pg.execute("select count(*) from lakefed_ingest.control where task_collection = 'b1'").fetchall() == [(0,)]


def test_two_connections_to_one_server_and_a_host_are_one_database_to_the_rule(pg, monkeypatch):
    monkeypatch.setattr(main, "_connection_endpoints", lambda wanted=(): {"conn_a": "srv:1433", "conn_b": "srv:1433"})
    first = _job(pg, "First", ["a1"])
    _add_through(pg, "a1", "conn_a", "db", "sales")
    _job(pg, "Second", ["b1"])
    with pytest.raises(main.HTTPException) as refused:              # through the other connection, same schema
        _check(pg, [_add_through(pg, "b1", "conn_b", "db", "sales")])
    assert refused.value.detail["jobs"] == [{"id": first, "name": "First"}]
    _job(pg, "Third", ["c1"])
    with pytest.raises(main.HTTPException):                          # by host and port, same schema
        _check(pg, [_add_host(pg, "c1", "srv", "db", "sales")])
    monkeypatch.setattr(main, "_connection_endpoints", lambda wanted=(): {})
    _job(pg, "Fourth", ["d1"])
    _check(pg, [_add_through(pg, "d1", "conn_c", "db", "sales")], confirm_shared=True)   # unseen: its own server


def test_a_database_new_to_the_job_that_another_job_reads_is_asked_about_once(pg):
    first = _job(pg, "First", ["a1"])
    _add(pg, "a1", "db", "sales")
    _job(pg, "Second", ["b1"])
    with pytest.raises(main.HTTPException) as asked:
        _check(pg, [_add(pg, "b1", "db", "copy")], confirm_shared=False)
    assert asked.value.detail["confirm_shared"] is True
    assert asked.value.detail["shares"] == [{"database": "db", "job_id": first, "job": "First", "unchecked": False}]
    _check(pg, [_add(pg, "b1", "db", "copy", table="u")], confirm_shared=False)   # Second reads db already: not asked


# ---- Create and Restore through the API, as the app's role ----------------------------------------------------------

def test_a_second_create_on_the_same_database_is_refused_into_the_same_schema_and_confirmed_into_another(client):
    did = _discover(client, ["eng_001"])
    selection = {"discovery_id": did, "databases": [{"server": "sql_a", "database": "eng_001"}]}
    made = client.post("/api/ingestion-jobs", json={"name": "First", "settings": SETTINGS, "dry_run": False,
                                                    "request_id": "shared-1", "source": selection})
    assert made.status_code == 200, made.text
    first = made.json()["id"]
    before = client.sql("select count(*) from lakefed_ingest.control")

    same = client.post("/api/ingestion-jobs", json={"name": "Second", "settings": SETTINGS, "dry_run": False,
                                                    "request_id": "shared-2", "source": selection})
    assert same.status_code == 409, same.text
    assert same.json()["detail"]["jobs"] == [{"id": first, "name": "First"}]
    assert "which writes to main.eng too" in same.json()["detail"]["message"]

    other = {**SETTINGS, "sink_schema": "eng_copy"}
    preview = client.post("/api/ingestion-jobs", json={"name": "Second", "settings": other, "source": selection})
    assert preview.status_code == 200, preview.text
    assert preview.json()["shared"]["shares"] == [{"database": "eng_001", "job_id": first, "job": "First",
                                                   "unchecked": False}]
    asked = client.post("/api/ingestion-jobs", json={"name": "Second", "settings": other, "dry_run": False,
                                                     "request_id": "shared-3", "source": selection})
    assert asked.status_code == 409 and asked.json()["detail"]["confirm_shared"] is True, asked.text
    assert client.sql("select count(*) from lakefed_ingest.control") == before        # nothing written, no empty job
    assert client.sql("select count(*) from lakefed_ingest.schedule where name = 'Second'") == [(0,)]

    confirmed = client.post("/api/ingestion-jobs", json={"name": "Second", "settings": other, "dry_run": False,
                                                         "request_id": "shared-4", "confirm_shared": True,
                                                         "source": selection})
    assert confirmed.status_code == 200, confirmed.text
    second = confirmed.json()["id"]
    by_job = client.sql("select m.schedule_id, lower(c.sink_schema), count(*) from lakefed_ingest.control c "
                        "join lakefed_ingest.schedule_member m on m.task_collection = c.task_collection "
                        "group by 1, 2 order by 1")
    assert by_job == [(first, "eng", before[0][0]), (second, "eng_copy", before[0][0])]   # the first job untouched


def test_a_restore_into_the_schema_a_job_reading_its_database_writes_is_refused_and_one_into_another_is_asked(
        client, monkeypatch):
    monkeypatch.setattr(main.jobs, "DELETE_JOB_ID", "99")
    did = _discover(client, ["eng_001"])
    selection = {"discovery_id": did, "databases": [{"server": "sql_a", "database": "eng_001"}]}
    kept = client.post("/api/ingestion-jobs", json={"name": "Kept", "settings": SETTINGS, "dry_run": False,
                                                    "request_id": "restore-1", "source": selection}).json()["id"]
    other = {**SETTINGS, "sink_schema": "eng_copy"}
    copy = client.post("/api/ingestion-jobs", json={"name": "Copy", "settings": other, "dry_run": False,
                                                    "confirm_shared": True, "request_id": "restore-2",
                                                    "source": selection})
    assert copy.status_code == 200, copy.text
    assert client.post(f"/api/schedules/{kept}/delete", json={"drop_targets": False}).status_code == 200

    # While Kept is deleted, Copy is moved into Kept's schema (by hand here; the rule refuses no write while Kept is
    # not live), so Kept's Restore would leave the two jobs on one database writing to one schema.
    # Its tables get names of their own there, so only the schema is in common, not a target table.
    client.owner.execute("update lakefed_ingest.control c set sink_schema = 'eng', sink_table = c.sink_table || '_copy' "
                         "from lakefed_ingest.schedule_member m where m.task_collection = c.task_collection "
                         "and m.schedule_id = %s", (copy.json()["id"],))
    refused = client.post(f"/api/schedules/{kept}/restore")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["jobs"] == [{"id": copy.json()["id"], "name": "Copy"}]
    assert client.sql("select deleted_at is not null from lakefed_ingest.schedule where id = %s", (kept,)) == [(True,)]
    assert client.sql("select count(*) from lakefed_ingest.schedule_member where schedule_id = %s "
                      "and removed_at is null", (kept,)) == [(0,)]

    client.owner.execute("update lakefed_ingest.control c set sink_schema = 'eng_copy' "
                         "from lakefed_ingest.schedule_member m where m.task_collection = c.task_collection "
                         "and m.schedule_id = %s", (copy.json()["id"],))
    asked = client.post(f"/api/schedules/{kept}/restore")          # into another schema now: asked first
    assert asked.status_code == 409 and asked.json()["detail"]["confirm_shared"] is True, asked.text
    assert client.sql("select deleted_at is not null from lakefed_ingest.schedule where id = %s", (kept,)) == [(True,)]
    restored = client.post(f"/api/schedules/{kept}/restore?confirm_shared=true")
    assert restored.status_code == 200, restored.text


def test_add_tables_of_a_database_another_job_reads_asks_first_and_writes_nothing_until_confirmed(client):
    did = _discover(client, ["eng_001", "eng_002"])
    one = lambda database: {"discovery_id": did, "databases": [{"server": "sql_a", "database": database}]}
    first = client.post("/api/ingestion-jobs", json={"name": "First", "settings": SETTINGS, "dry_run": False,
                                                     "request_id": "add-1", "source": one("eng_001")}).json()["id"]
    second = client.post("/api/ingestion-jobs", json={"name": "Second", "settings": {**SETTINGS, "sink_schema": "copy"},
                                                      "dry_run": False, "request_id": "add-2",
                                                      "source": one("eng_002")}).json()["id"]
    before = client.sql("select count(*) from lakefed_ingest.control")
    asked = client.post(f"/api/ingestion-jobs/{second}/tables", json={"source": one("eng_001"), "dry_run": False,
                                                                      "request_id": "add-3"})
    assert asked.status_code == 409 and asked.json()["detail"]["confirm_shared"] is True, asked.text
    assert asked.json()["detail"]["jobs"] == [{"id": first, "name": "First"}]
    assert client.sql("select count(*) from lakefed_ingest.control") == before
    assert client.sql("select count(*) from lakefed_ingest.app_task where request_id = 'add-3'") == [(0,)]
    added = client.post(f"/api/ingestion-jobs/{second}/tables", json={"source": one("eng_001"), "dry_run": False,
                                                                      "request_id": "add-3", "confirm_shared": True})
    assert added.status_code == 200, added.text
    assert client.sql("select count(*) from lakefed_ingest.control c join lakefed_ingest.schedule_member m "
                      "on m.task_collection = c.task_collection where m.schedule_id = %s and c.src_database = "
                      "'eng_001' and lower(c.sink_schema) = 'copy'", (second,))[0][0] > 0


def _source(database, secret="k1"):
    return {"src_type": "sqlserver", "src_host": "h", "src_port": 1433, "secret_key": secret, "src_database": database}


def _add(conn, group, database, schema, table="t", secret="k1", catalog="m"):
    """A control row of a database on host h, writing to catalog.schema; returns it as the write's rows are, with its
    id."""
    row = {**_source(database, secret), "task_collection": group, "sink_catalog": catalog, "sink_schema": schema}
    row["id"] = conn.execute(
        "insert into lakefed_ingest.control (task_collection, src_host, src_port, secret_key, src_database, src_schema, "
        "src_table, sink_catalog, sink_schema, sink_table, primary_key, select_list) values "
        "(%s, 'h', 1433, %s, %s, 'dbo', %s, %s, %s, %s, 'id', 'id') returning id",
        (group, secret, database, table, catalog, schema, f"{group}_{database}_{table}")).fetchone()[0]
    return row


def _add_through(conn, group, connection, database, schema, table="t"):
    """A control row read through a Unity Catalog connection (no host or secret)."""
    row = {"task_collection": group, "src_type": "sqlserver", "src_connection": connection, "use_remote_query": True,
           "src_database": database, "sink_catalog": "m", "sink_schema": schema}
    row["id"] = conn.execute(
        "insert into lakefed_ingest.control (task_collection, src_connection, use_remote_query, src_database, "
        "src_schema, src_table, sink_catalog, sink_schema, sink_table, primary_key, select_list) values "
        "(%s, %s, true, %s, 'dbo', %s, 'm', %s, %s, 'id', 'id') returning id",
        (group, connection, database, table, schema, f"{group}_{connection}_{database}_{table}")).fetchone()[0]
    return row


def _add_host(conn, group, host, database, schema, table="t"):
    """A control row read by host, at the default port."""
    row = {"task_collection": group, "src_type": "sqlserver", "src_host": host, "src_port": None, "secret_key": "k9",
           "src_database": database, "sink_catalog": "m", "sink_schema": schema}
    row["id"] = conn.execute(
        "insert into lakefed_ingest.control (task_collection, src_host, secret_key, src_database, src_schema, "
        "src_table, sink_catalog, sink_schema, sink_table, primary_key, select_list) values "
        "(%s, %s, 'k9', %s, 'dbo', %s, 'm', %s, %s, 'id', 'id') returning id",
        (group, host, database, table, schema, f"{group}_{host}_{database}_{table}")).fetchone()[0]
    return row


def _check(conn, rows, confirm_shared=True):
    main._refuse_shared_targets(conn.cursor(), {row["task_collection"] for row in rows}, rows,
                                [row["id"] for row in rows], confirm_shared=confirm_shared)
