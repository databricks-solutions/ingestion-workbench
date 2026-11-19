"""The whole server less some databases: Discover's server row ticked, then some of its databases unticked. The rest
stay chosen, the server row shows as chosen in part (a filled square, as Catalog Explorer's and the Jobs list's
selection show it), and the databases unticked stay out through Create, Add tables, the background prepare and a
re-discovery. These tests use fakes; large_writes_pg_test.py runs the selection's SQL against Postgres."""
import pathlib

import pytest

import by_database as bd
import queries as q
import rediscovery as rd

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import main  # noqa: E402

DISCOVER = pathlib.Path(__file__).resolve().parents[1] / "src" / "frontend" / "src" / "pages" / "Discover.tsx"


def _server_less_eng_002(**more) -> "main.Selection":
    return main.Selection(discovery_ids=["d1"], whole_server=True, filter="eng",
                          excluded_databases=[{"server": "sql_a", "database": "ENG_002"}], **more)


def test_a_database_the_whole_server_covers_can_be_unticked_on_the_discover_step():
    discover = DISCOVER.read_text()
    database_tick = discover[discover.index("label={`The whole database ${d}`}"):]
    database_tick = database_tick[:database_tick.index("/>")]
    assert "!covered(d) &&" not in database_tick, "a database under a ticked server must take its own tick"


def test_the_databases_the_whole_server_leaves_out_are_no_units_of_the_job():
    matched = [("sql_a", "eng_001"), ("sql_a", "eng_002"), ("sql_a", "eng_003")]
    units = [u.database for u in bd.selection_units(_server_less_eng_002(), iter(matched))]
    assert units == ["eng_001", "eng_003"]


def test_a_database_left_out_of_the_whole_server_can_still_give_some_of_its_tables():
    selection = _server_less_eng_002(tables=[{"server": "sql_a", "database": "eng_002", "tables": ["gen.t1"]}])
    units = [(u.database, u.every) for u in bd.selection_units(selection, iter([("sql_a", "eng_001")]))]
    assert units == [("eng_001", True), ("eng_002", False)]


def test_what_the_job_was_chosen_from_keeps_the_databases_left_out():
    parts = main._selection_parts(_server_less_eng_002(), ["sql_a"])
    assert parts == [("server", "sql_a", None, None, "eng"), ("excluded_database", "sql_a", "ENG_002", None, None)]


def test_the_selections_queries_leave_out_the_databases_left_out():
    _, _, _, rest = main._selection_params(_server_less_eng_002())
    assert rest == (True, "eng", [q.selection_key("sql_a", "eng_002")], [], [])
    assert "<> all(%s)" in q.selected_count_sql("discovery")


def test_a_job_read_back_from_its_selection_still_leaves_them_out(monkeypatch):
    parts = [{"scope": "server", "server": "sql_a", "src_database": None, "tables": None, "filter": "eng",
              "source_kind": "discovery", "source_id": "d1"},
             {"scope": "excluded_database", "server": "sql_a", "src_database": "eng_002", "tables": None, "filter": None,
              "source_kind": "discovery", "source_id": "d1"}]
    monkeypatch.setattr(main.db, "query", lambda sql, params=(): parts)
    selection = main._stored_selection(1, {"source": {"kind": "discovery", "source_id": "d1"}})
    assert selection.whole_server and [(d.server, d.database) for d in selection.excluded_databases] == [
        ("sql_a", "eng_002")]


def test_a_selection_kept_before_databases_could_be_left_out_reads_as_before(monkeypatch):
    parts = [{"scope": "server", "server": "sql_a", "src_database": None, "tables": None, "filter": "eng",
              "source_kind": "discovery", "source_id": "d1"}]
    monkeypatch.setattr(main.db, "query", lambda sql, params=(): parts)
    selection = main._stored_selection(1, {"source": {"kind": "discovery", "source_id": "d1"}})
    assert selection.whole_server and selection.filter == "eng" and selection.excluded_databases == []


def test_create_checks_only_the_databases_chosen(monkeypatch):
    rows = [{"server": "sql_a", "src_connection": "sql_a", "src_database": name, "listing": {}}
            for name in ("eng_001", "eng_002", "eng_003")]
    monkeypatch.setattr(main.db, "query", lambda sql, params=(): rows)
    chosen = main._chosen_databases(_server_less_eng_002(), "discovery", ["d1"])
    assert [row["src_database"] for row in chosen] == ["eng_001", "eng_003"]


def _found(database: str, table: str = "t1") -> dict:
    return {"server": "h1", "src_database": database, "src_schema": "dbo", "src_table": table, "fields": {}}


def test_a_re_discovery_does_not_add_a_database_the_whole_server_leaves_out():
    parts = [{"scope": "server", "server": "h1", "src_database": None, "tables": None, "filter": ""},
             {"scope": "excluded_database", "server": "h1", "src_database": "ENG_002", "tables": None, "filter": None}]
    found = [_found("eng_001"), _found("eng_002"), _found("eng_003")]
    statuses = [{"server": "h1", "src_database": d, "status": "ok", "error": None} for d in ("eng_001", "eng_003")]
    statuses.append({"server": "h1", "src_database": "eng_002", "status": "failed", "error": "login failed"})
    picked = rd.wanted(parts, found, statuses, [])
    assert [r["src_database"] for r in picked["add"]] == ["eng_001", "eng_003"]
    assert picked["new_databases"] == ["h1/eng_001", "h1/eng_003"]
    assert picked["errors"] == [], "a database left out is not the job's, so its read failing is not reported"


def test_what_discover_hands_to_new_job_and_add_tables_names_the_databases_unticked_under_the_server():
    discover = DISCOVER.read_text()
    assert discover.count("excluded_databases: excludedDatabases,") == 2, "both Add paths: drafts (New job) and Edit"
    assert "wholeServer.excluded.map((d) => ({ server: connection, database: d }))" in discover


def test_a_database_unticked_under_the_server_leaves_the_servers_draft_and_ticked_again_comes_back():
    discover = DISCOVER.read_text()
    sync = discover[discover.index("const syncDrafts = () => {"):discover.index("const wholeKey")]
    assert "await api.post(`/discoveries/${encodeURIComponent(server.id)}/drop`, { databases: leaving });" in sync
    assert 'kind: "tables", databases: back, draft_id: server.id' in sync
    assert "excludedKey]);" in discover, "the drafts follow each untick and tick under the server"


def test_add_tables_is_not_stopped_by_a_database_unticked_under_the_server_that_couldnt_be_read():
    discover = DISCOVER.read_text()
    assert "const failedChosen = got.failed.filter(([d]) => !excludedFrom(wholeServer, d));" in discover


def test_new_jobs_chosen_tree_shows_the_databases_unticked_under_the_server_as_left_out():
    chosen = (DISCOVER.parent / "ChosenTree.tsx").read_text()
    assert "source.excluded_databases" in chosen and "coveredByServer(wholeServer, database)" in chosen
    assert "excludedFrom(wholeServer, String(d.src_database))" in chosen and '"num muted">left out<' in chosen


def test_a_database_unticked_by_one_add_tables_is_still_taken_by_a_whole_server_chosen_without_it():
    first = {"scope": "server", "server": "h1", "src_database": None, "tables": None, "filter": "", "created_at": "t1"}
    later = {**first, "filter": "eng", "created_at": "t2"}
    unticked = {"scope": "excluded_database", "server": "h1", "src_database": "eng_002", "tables": None, "filter": None,
                "created_at": "t2"}
    statuses = [{"server": "h1", "src_database": "eng_002", "status": "ok", "error": None}]
    picked = rd.wanted([first, later, unticked], [_found("eng_002")], statuses, [])
    assert picked["new_databases"] == ["h1/eng_002"], "the first whole server, chosen with nothing unticked, takes it"
    assert rd.wanted([later, unticked], [_found("eng_002")], statuses, [])["new_databases"] == []


def _failed_read(database: str) -> dict:
    return {"server": "sql_a", "src_database": database, "error": "Read timed out", "whole_server": True}


def test_create_neither_waits_nor_stops_for_a_database_unticked_under_the_server_that_couldnt_be_read(monkeypatch):
    reads = [{"discovery_id": "d1", "src_connection": "sql_a", "databases": 3, "pending": 0, "done": 2, "failed": 1,
              "discarded": 0, "tables": 20}]
    monkeypatch.setattr(main.db, "query", lambda sql, params=(): reads if sql == q.draft_reads_sql()
                        else [_failed_read("eng_002")] if sql == q.draft_failed_sql() else [])
    reading = main._draft_reading(_server_less_eng_002(), advance=False)
    assert reading["failed"] == 0 and reading["failed_databases"] == [] and main._draft_problems(reading) == []
    chosen = main.Selection(discovery_ids=["d1"], whole_server=True, filter="eng")
    assert main._draft_reading(chosen, advance=False)["failed_databases"] == [["eng_002", "Read timed out"]]


def test_the_jobs_failed_reads_leave_out_a_database_unticked_under_the_server(monkeypatch):
    monkeypatch.setattr(main, "_job_discovery_ids", lambda job: ["d1"])
    monkeypatch.setattr(main.db, "query", lambda sql, params=(): [_failed_read("eng_002"), _failed_read("eng_003")])
    failed = main._failed_reads({"id": 7, "options": {}}, _server_less_eng_002())
    assert [entry["database"] for entry in failed] == ["eng_003"]


def test_select_all_shown_takes_back_the_databases_unticked_under_the_server_rather_than_ticking_them_apart():
    discover = DISCOVER.read_text()
    select_all = discover[discover.index("const selectAllShown = () => {"):discover.index("const openDatabase")]
    assert "setWholeServer(takeBack(wholeServer, back));" in select_all
    assert '!back.includes(d)), "all");' in select_all
    assert "<button className=\"ghost\" onClick={selectAllShown}>Select all shown</button>" in discover


def test_the_counts_ask_for_each_database_unticked_under_the_server_by_name_whichever_page_is_shown():
    discover = DISCOVER.read_text()
    assert '["discovery-listing", discoveryId, "unticked", wholeServer.excluded.join("\\n")]' in discover
    assert "excluded: wholeServer.excluded.filter((d) => !closed(d)).map(tablesOf)" in discover
