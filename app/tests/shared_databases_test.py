"""The shared-database rule without a database: shared_databases (what a target is, the verdict before a Create and
its wording), and main's checks over a scripted cursor and stubbed reads (which reads run, and when the rule refuses).
Its reads against Postgres are shared_sources_pg_test.py's."""
import pytest

pytest.importorskip("fastapi")
import main  # noqa: E402
import queries as q  # noqa: E402
import shared_databases as sb  # noqa: E402

@pytest.fixture(autouse=True)
def no_connections(monkeypatch):
    """The workspace's connections, as the rule reads them: none here, unless a test says otherwise."""
    monkeypatch.setattr(main, "_connection_endpoints", lambda wanted=(): {})


ROW = {"task_collection": "b1", "src_type": "sqlserver", "src_host": "h", "src_port": 1433, "secret_key": "k1",
       "src_database": "db", "sink_catalog": "Main", "sink_schema": "Copy"}


def test_a_target_is_the_lower_case_catalog_and_schema_and_unknown_while_either_is_a_template_or_blank():
    assert sb.target_of(ROW) == "main.copy"
    assert sb.target_of({**ROW, "sink_schema": "{database}"}) is None
    assert sb.target_of({**ROW, "sink_catalog": " "}) is None


def test_a_new_jobs_target_for_a_database_is_filled_from_its_server_and_name_and_unknown_when_it_needs_a_table():
    database = {"src_database": "Sales-01", "src_connection": "sql_a", "src_type": "sqlserver"}
    assert sb.planned_target({"sink_catalog": "main", "sink_schema": "{database}"}, database) == "main.sales-01"   # Unity Catalog keeps a hyphen, as a new job names it
    assert sb.planned_target({"sink_catalog": "main", "sink_schema": "{server}"}, database) == "main.sql_a"
    assert sb.planned_target({"sink_catalog": "main", "sink_schema": "{schema}"}, database) is None
    assert sb.planned_target({"sink_catalog": "main", "sink_schema": ""}, database) is None


def test_the_verdict_is_the_whole_jobs_a_target_of_any_database_meeting_the_other_jobs_refuses():
    owners = [sb.Owner(key="k-a", database="a", job_id=3, job="First"),
              sb.Owner(key="k-b", database="b", job_id=3, job="First"),
              sb.Owner(key="k-c", database="c", job_id=4, job="Other")]
    planned = {"k-a": {"main.copy"}, "k-b": {"main.copy"}, "k-d": {"main.sales"}}
    verdict = sb.judge(owners, {3: {"main.sales"}}, planned)     # d, read by no one else, goes to First's schema
    assert [(c.database, c.job, c.target) for c in verdict.clashes] == [("a", "First", "main.sales"),
                                                                         ("b", "First", "main.sales")]
    assert [s.as_dict() for s in verdict.shares] == [{"database": "c", "job_id": 4, "job": "Other", "unchecked": False}]


def test_a_share_is_unchecked_while_some_target_of_the_job_is_not_known_yet():
    owners = [sb.Owner(key="k-a", database="a", job_id=3, job="First")]
    verdict = sb.judge(owners, {}, {"k-a": {"main.copy"}, "k-b": {None}})
    assert verdict.clashes == [] and [s.unchecked for s in verdict.shares] == [True]


def test_a_refusal_names_the_first_clash_counts_the_rest_and_links_each_job_once():
    clashes = [sb.Clash(database="a", job_id=3, job="First", target="main.sales"),
               sb.Clash(database="b", job_id=3, job="First", target="main.sales")]
    detail = sb.refusal(clashes)
    assert detail["message"].startswith("database a is also in the ingestion job First, which writes to main.sales "
                                        "too: a second job may read a database only into a different catalog or schema")
    assert detail["message"].endswith("(1 more clash like it)")
    assert detail["jobs"] == [{"id": 3, "name": "First"}] and detail["reason"] == "shared_target"


def test_a_confirmation_lists_the_shares_with_their_jobs_and_asks_for_confirm_shared():
    shares = [sb.Share(database="b", job_id=3, job="First")]
    detail = sb.confirmation(shares)
    assert detail["confirm_shared"] is True and detail["jobs"] == [{"id": 3, "name": "First"}]
    assert detail["shares"] == [{"database": "b", "job_id": 3, "job": "First", "unchecked": False}]


def test_a_create_is_refused_for_a_clash_asked_for_a_share_and_goes_on_once_confirmed():
    clash = sb.Verdict(clashes=[sb.Clash(database="a", job_id=3, job="First", target="main.sales")])
    share = sb.Verdict(shares=[sb.Share(database="b", job_id=3, job="First")])
    with pytest.raises(main.HTTPException) as refused:
        main._raise_for_shared(clash, confirm_shared=True)                  # no confirmation helps a clash
    assert refused.value.detail["reason"] == "shared_target"
    with pytest.raises(main.HTTPException) as asked:
        main._raise_for_shared(share, confirm_shared=False)
    assert asked.value.status_code == 409 and asked.value.detail["confirm_shared"] is True
    main._raise_for_shared(share, confirm_shared=True)
    main._raise_for_shared(sb.Verdict(), confirm_shared=False)


def test_the_verdict_before_a_create_reads_the_owners_by_key_then_only_their_jobs_targets(monkeypatch):
    asked = []

    def query(sql, params=()):
        asked.append(sql)
        if sql == q.live_database_owners_sql():
            return [{"key": sb.server_key(ROW, {}), "database": "db", "job_id": 3, "job": "First"}]
        if sql == q.jobs_writing_targets_sql():
            assert params[2] == [3]
            wanted = {f"{c}.{s}" for c, s in zip(params[0], params[1])}
            return [{"job_id": 3, "target": "main.copy"}] if "main.copy" in wanted else []
        raise AssertionError(sql)

    monkeypatch.setattr(main.db, "query", query)
    verdict = main._shared_verdict([ROW])
    assert [(c.database, c.target) for c in verdict.clashes] == [("db", "main.copy")]
    other = main._shared_verdict([{**ROW, "src_database": "db", "sink_schema": "{database}"}],
                                 settings={"sink_catalog": "main", "sink_schema": "elsewhere"})
    assert other.clashes == [] and [s.job for s in other.shares] == ["First"]
    assert asked == [q.live_database_owners_sql(), q.jobs_writing_targets_sql()] * 2


def test_no_owner_means_no_read_of_targets(monkeypatch):
    asked = []
    monkeypatch.setattr(main.db, "query", lambda sql, params=(): asked.append(sql) or [])
    assert main._shared_verdict([ROW]).shares == [] and asked == [q.live_database_owners_sql()]
    assert main._shared_verdict([{"src_database": None}]).shares == [] and len(asked) == 1


class ScriptedCursor:
    """A cursor that answers each statement from a script: {sql: rows, or rows_of(params)}; any other statement
    fails the test, so the reads the check makes are exactly those listed."""

    def __init__(self, script):
        self.script, self.ran, self._rows = script, [], []

    def execute(self, sql, params=()):
        if sql == q.shared_rule_lock_sql():
            self.ran.append((sql, params))
            self._rows = [("",)]
            return
        if sql not in self.script:
            raise AssertionError(f"unexpected statement: {sql[:80]}")
        answer = self.script[sql]
        self.ran.append((sql, params))
        self._rows = list(answer(params) if callable(answer) else answer)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_a_write_to_groups_of_no_live_job_reads_nothing_more():
    cursor = ScriptedCursor({q.job_groups_of_sql(): []})
    main._refuse_shared_targets(cursor, ["b1"], [ROW], [11], confirm_shared=False)
    assert [sql for sql, _ in cursor.ran] == [q.job_groups_of_sql()]
    main._refuse_shared_targets(ScriptedCursor({}), [None], [ROW], [11], confirm_shared=False)   # no group: no read


def test_a_database_another_job_reads_into_another_schema_is_asked_then_passes_and_into_a_common_one_is_refused():
    def run(common, confirm_shared=True):
        cursor = ScriptedCursor({
            q.job_groups_of_sql(): [(5, "b1"), (5, "b2")],
            q.new_shared_databases_sql(): [("db", 3, "First")],
            q.common_target_sql(): common,
            q.new_shared_targets_sql(): []})
        main._refuse_shared_targets(cursor, ["b1"], [ROW], [11], confirm_shared=confirm_shared)
        return cursor

    with pytest.raises(main.HTTPException) as asked:
        run([], confirm_shared=False)
    assert asked.value.detail["confirm_shared"] is True
    assert asked.value.detail["shares"] == [{"database": "db", "job_id": 3, "job": "First", "unchecked": False}]
    cursor = run([])
    scope = cursor.ran[2][1]
    assert scope["keys"] == [sb.server_key(ROW, {})] and scope["groups"] == ["b1", "b2"] and scope["jobs"] == [5]
    assert scope["ids"] == [11] and scope["whole"] is False
    assert cursor.ran[1][0] == q.shared_rule_lock_sql()                   # locked before anything is read
    assert cursor.ran[3][1] == (3, ["b1", "b2"], ["b1", "b2"])          # First's own targets against the whole job's
    assert cursor.ran[4][1]["catalogs"] == ["main"] and cursor.ran[4][1]["schemas"] == ["copy"]
    with pytest.raises(main.HTTPException) as refused:
        run([("main.copy",)])
    assert refused.value.status_code == 409 and refused.value.detail["jobs"] == [{"id": 3, "name": "First"}]
    assert "database db is also in the ingestion job First, which writes to main.copy too" in refused.value.detail[
        "message"]


def test_a_new_target_another_job_writes_is_refused_only_when_the_jobs_share_a_database():
    def run(shared):
        cursor = ScriptedCursor({
            q.job_groups_of_sql(): [(5, "b1")],
            q.new_shared_databases_sql(): [],
            q.new_shared_targets_sql(): [("main.copy", 3, "First")],
            q.common_database_sql(): shared})
        main._refuse_shared_targets(cursor, ["b1"], [ROW], [11], confirm_shared=False)
        return cursor

    assert run([])                                                     # fan-in from other databases: allowed
    with pytest.raises(main.HTTPException) as refused:
        run([("db",)])
    assert "database db is also in the ingestion job First, which writes to main.copy too" in refused.value.detail[
        "message"]


def test_a_job_judged_for_its_database_is_not_judged_again_for_its_target():
    cursor = ScriptedCursor({
        q.job_groups_of_sql(): [(5, "b1")],
        q.new_shared_databases_sql(): [("db", 3, "First")],
        q.common_target_sql(): [],
        q.new_shared_targets_sql(): [("main.copy", 3, "First")]})
    main._refuse_shared_targets(cursor, ["b1"], [ROW], [11], confirm_shared=True)
    assert q.common_database_sql() not in [sql for sql, _ in cursor.ran]


def test_a_write_of_every_row_reads_the_groups_keys_and_targets_and_counts_each_as_new():
    key = sb.server_key(ROW, {})
    cursor = ScriptedCursor({
        q.job_groups_of_sql(): [(5, "b1")],
        q.groups_keys_and_targets_sql(): [("database", key, None), ("target", "main", "copy")],
        q.new_shared_databases_sql(): [],
        q.new_shared_targets_sql(): []})
    main._refuse_shared_targets(cursor, ["b1"], confirm_shared=True)
    by_sql = dict(cursor.ran)
    assert by_sql[q.new_shared_databases_sql()]["whole"] is True
    assert by_sql[q.new_shared_databases_sql()]["keys"] == [key]
    assert by_sql[q.new_shared_targets_sql()]["schemas"] == ["copy"]


def test_a_write_of_many_transactions_is_checked_whole_before_its_first_against_its_planned_rows(monkeypatch):
    key = sb.server_key(ROW, {})
    asked = []

    def query(sql, params=()):
        asked.append(sql)
        if sql == q.new_shared_databases_sql():
            assert params["ids"] == [] and params["whole"] is False and params["jobs"] == [5]
            return [{"database": "db", "job_id": 3, "job": "First"}]
        if sql == q.jobs_writing_targets_sql():
            return [{"job_id": 3, "target": "main.copy"}]        # First writes a target the plan has
        if sql == q.new_shared_targets_sql():
            return [{"target": "main.copy", "job_id": 3, "job": "First"}]      # judged already: not asked again
        raise AssertionError(sql)

    monkeypatch.setattr(main, "_job_groups", lambda job_id: ["b1"])
    monkeypatch.setattr(main.db, "query", query)
    with pytest.raises(main.HTTPException) as refused:
        main._refuse_shared_before(5, {"keys": {key}, "targets": {("main", "copy")}}, confirm_shared=True)
    assert refused.value.detail["jobs"] == [{"id": 3, "name": "First"}]
    assert asked == [q.new_shared_databases_sql(), q.jobs_writing_targets_sql(), q.new_shared_targets_sql()]


def test_a_new_job_planned_into_a_schema_another_job_writes_is_refused_when_it_reads_one_of_that_jobs_databases(
        monkeypatch):
    key = sb.server_key(ROW, {})

    def query(sql, params=()):
        if sql == q.new_shared_databases_sql():
            return []
        if sql == q.new_shared_targets_sql():
            return [{"target": "main.copy", "job_id": 3, "job": "First"}]
        if sql == q.live_database_owners_sql():
            return [{"key": key, "database": "db", "job_id": 3, "job": "First"}]
        raise AssertionError(sql)

    monkeypatch.setattr(main.db, "query", query)
    with pytest.raises(main.HTTPException):
        main._refuse_shared_before(None, {"keys": {key}, "targets": {("main", "copy")}}, confirm_shared=True)
    monkeypatch.setattr(main.db, "query", lambda sql, params=(): [])
    main._refuse_shared_before(None, {"keys": {key}, "targets": {("main", "copy")}}, confirm_shared=False)


def test_one_server_is_one_server_whether_read_by_host_or_through_either_of_two_connections():
    endpoints = {"conn_a": "srv.example:1433", "conn_b": "srv.example:1433"}
    by_host = {"src_host": "SRV.example", "src_port": None, "src_type": "sqlserver", "secret_key": "k1",
               "src_database": "db"}
    through_a = {"src_connection": "Conn_A", "src_database": "db"}
    through_b = {"src_connection": "conn_b", "src_database": "db"}
    keys = {sb.server_key(row, endpoints) for row in (by_host, {**by_host, "secret_key": "k2"}, through_a, through_b)}
    assert keys == {"srv.example:1433\x1fdb"}          # the login is no part of it, nor the connection's name
    assert sb.server_key(through_a, {}) == "connection:conn_a\x1fdb"            # a connection the app can't see
    assert sb.server_key({**by_host, "src_database": "other"}, endpoints) != sb.server_key(by_host, endpoints)


def test_a_connection_names_its_server_with_the_source_types_default_port_and_none_without_a_host():
    assert sb.server_of_connection({"host": "H", "port": "1444", "connection_type": "SQLSERVER"}) == "h:1444"
    assert sb.server_of_connection({"host": "pg", "connection_type": "POSTGRESQL"}) == "pg:5432"
    assert sb.server_of_connection({"host": "", "connection_type": "SQLSERVER"}) is None


def test_the_connections_are_listed_once_for_a_while_again_for_one_not_known_and_a_failure_keeps_the_list(monkeypatch):
    monkeypatch.undo()                                   # the real _connection_endpoints, with a fresh cache
    monkeypatch.setattr(main, "_endpoints_cache", {"at": 0.0, "map": {}})
    listed = []
    known = [{"name": "Conn_A", "host": "srv", "port": None, "connection_type": "SQLSERVER"},
             {"name": "nohost", "host": None, "connection_type": "SQLSERVER"},
             {"name": "odd", "host": "h2", "port": "abc", "connection_type": "SQLSERVER"}]
    monkeypatch.setattr(main, "_connections", lambda: listed.append(1) or list(known))
    assert main._connection_endpoints() == {"conn_a": "srv:1433", "odd": "h2:1433"}   # an odd port is the default
    assert main._connection_endpoints(["conn_a"]) == {"conn_a": "srv:1433", "odd": "h2:1433"} and len(listed) == 1
    known.append({"name": "conn_new", "host": "srv", "port": 1433, "connection_type": "SQLSERVER"})
    assert "conn_new" not in main._connection_endpoints(["conn_new"]) and len(listed) == 1     # not within 10 s
    main._endpoints_cache["at"] -= main.CONNECTIONS_RELIST_SECONDS + 1
    assert main._connection_endpoints(["conn_new"])["conn_new"] == "srv:1433" and len(listed) == 2
    main._endpoints_cache["at"] -= main.CONNECTIONS_CACHE_SECONDS + 1

    def failing():
        raise RuntimeError("the workspace is away")

    monkeypatch.setattr(main, "_connections", failing)
    assert main._connection_endpoints()["conn_a"] == "srv:1433"


def test_an_edit_of_a_load_type_or_a_key_is_not_checked_by_the_rule_and_one_of_a_target_is(monkeypatch):
    checked = []
    monkeypatch.setattr(main, "_refuse_shared_targets",
                        lambda cur, groups, rows=None, ids=(), *, confirm_shared: checked.append(sorted(ids)))
    for name in ("_refuse_connection_edits", "_rewrite_rollups", "_refuse_mixed_sinks", "_log"):
        monkeypatch.setattr(main, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_split_database_keys", lambda cur, rows: set())
    monkeypatch.setattr(main, "_split_database_warnings", lambda cur, rows, already: [])
    rows = [{**ROW, "id": 1}, {**ROW, "id": 2}]
    plans = [{"id": 1, "changes": {"load_type": ["ct", "full"]}, "reseed": None},
             {"id": 2, "changes": {"sink_schema": ["copy", "other"]}, "reseed": None}]
    main._apply_plans(ScriptedCursor({q.update_control_sql(["load_type"]): [], q.update_control_sql(["sink_schema"]): []}),
                      rows, plans, "op", "b1", confirm_shared=False)
    assert checked == [[2]]


def test_the_rules_reads_carry_the_connections_servers_in_the_same_order():
    assert main._endpoint_params({"b": "s2:1", "a": "s1:1"}) == {"ep_names": ["a", "b"], "ep_servers": ["s1:1", "s2:1"]}
