"""The warning for a shared target whose tables load in different ways (mixed_load.py, main._mixed_load_warnings). Every
write goes through; the warning comes back with it, names the targets (first three, then a count) and what a full
reload table costs a shared target, and a Create's preview shows the same warning before anything is written. Plain: a
fake cursor stands in for the database."""
import re
from pathlib import Path

import pytest

import mixed_load as ml
import new_job as nj
import preparing
import queries as q

pytest.importorskip("fastapi")
import main  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "src" / "frontend" / "src"
REWRITES = ("Each run of a full reload table deletes and rewrites all of its rows in the target, even when nothing "
            "changed, so the target's change feed and any stream reading it get those rows again every run.")
APPENDS = ("Each run of a full reload table appends all of its rows to the target again, even when nothing changed, "
           "so the target grows by those rows every run.")
ADVICE = ("The data stays correct. To avoid it, give the full reload tables a target of their own, or turn on Change "
          "Tracking for them at the source and read the tables again.")


def shared(load_type, table="orders", client="db1", write_mode="merge", schema="bronze"):
    return {"sink_catalog": "main", "sink_schema": schema, "sink_table": table, "client_id": client,
            "load_type": load_type, "write_mode": write_mode}


def found(target, full=1, other=1, append=False):
    return {"target": target, "full": full, "other": other, "append": append}


class FakeCursor:
    """Answers the mixed-load check with ``answer`` (rows of sink, full, other, append) and records the statements."""

    def __init__(self, answer=()):
        self.answer, self.statements, self.params = list(answer), [], []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        self.params.append(params)

    def fetchall(self):
        return self.answer


# ---- the rule ----------------------------------------------------------------------------------------

def test_a_full_reload_and_a_change_tracked_table_on_one_shared_target_are_a_mix_with_their_counts():
    rows = [shared("ct", client="a"), shared("ct", client="b"), shared("full", client="c")]
    assert ml.mixed_targets(rows) == [found("main.bronze.orders", full=1, other=2)]


@pytest.mark.parametrize("other", ["ct", "cursor", "cdc"])
def test_a_full_reload_with_any_other_load_type_is_a_mix(other):
    assert [t["target"] for t in ml.mixed_targets([shared("full", client="a"), shared(other, client="b")])] == [
        "main.bronze.orders"]


def test_change_tracked_with_cursor_all_full_and_all_change_tracked_targets_are_no_mix():
    assert ml.mixed_targets([shared("ct", client="a"), shared("cursor", client="b")]) == []
    assert ml.mixed_targets([shared("full", client="a"), shared("full", client="b")]) == []
    assert ml.mixed_targets([shared("ct", client="a"), shared("ct", client="b")]) == []


def test_a_dedicated_target_is_no_mix_and_its_row_does_not_count_toward_one():
    assert ml.mixed_targets([shared("full", client=None), shared("ct", client=None)]) == []
    assert ml.mixed_targets([shared("full", client=""), shared("ct", client="b")]) == []


def test_a_preview_whose_target_is_not_typed_yet_names_no_target():
    blank = [dict(shared("full", client="a"), sink_table=""), dict(shared("ct", client="b"), sink_table="")]
    assert ml.mixed_targets(blank) == []


def test_targets_are_matched_without_regard_to_case_and_are_named_in_order():
    rows = [shared("full", table="Orders", client="a"), shared("ct", table="orders", client="b"),
            shared("full", table="Lines", client="a"), shared("ct", table="LINES", client="b")]
    assert [t["target"] for t in ml.mixed_targets(rows)] == ["main.bronze.lines", "main.bronze.orders"]


def test_a_target_is_marked_append_when_a_full_reload_table_on_it_appends():
    appends = ml.mixed_targets([shared("full", client="a", write_mode="append"), shared("ct", client="b")])
    merges = ml.mixed_targets([shared("full", client="a"), shared("ct", client="b", write_mode="append")])
    assert appends[0]["append"] is True and merges[0]["append"] is False


# ---- the words ---------------------------------------------------------------------------------------

def test_one_target_names_its_counts_what_a_full_reload_costs_and_the_way_out():
    assert ml.message([found("main.bronze.orders", full=2, other=3)], 1, 0) == (
        "Target main.bronze.orders is shared by tables with different load types: 2 full reload and 3 "
        f"change-tracked or cursor. {REWRITES} {ADVICE}")


def test_several_targets_name_three_and_count_the_rest():
    targets = [found(f"main.bronze.t{n}") for n in range(5)]
    assert ml.message(targets, 5, 0) == (
        "5 targets are shared by tables with different load types: main.bronze.t0, main.bronze.t1, main.bronze.t2 "
        f"and 2 more. {REWRITES} {ADVICE}")
    assert "and 1 more" in ml.message(targets[:3] + [found("main.bronze.t3")], 4, 0)


def test_three_targets_are_named_without_a_count():
    targets = [found(f"main.bronze.t{n}") for n in range(3)]
    assert ml.message(targets, 3, 0).startswith(
        "3 targets are shared by tables with different load types: main.bronze.t0, main.bronze.t1, main.bronze.t2. ")


def test_an_append_target_says_it_grows_instead_of_that_it_is_rewritten():
    assert APPENDS in ml.message([found("main.bronze.orders", append=True)], 1, 1)
    assert REWRITES not in ml.message([found("main.bronze.orders", append=True)], 1, 1)
    both = ml.message([found("a.b.c"), found("a.b.d", append=True)], 2, 1)
    assert REWRITES in both and "A target in append mode takes those rows again instead" in both


def test_the_message_follows_the_app_wording_rules():
    text = ml.message([found("main.bronze.orders", full=2, other=3)], 1, 0)
    assert "—" not in text and not re.search(r"\bcollections?\b", text, re.IGNORECASE)
    assert "Change Tracking" in text


# ---- the warning -------------------------------------------------------------------------------------

def test_a_mix_comes_back_as_a_warning_not_a_refusal(monkeypatch):
    cursor = FakeCursor([("main.bronze.orders", 2, 3, 0)])
    [warning] = main._mixed_load_warnings(cursor, [shared("full")])
    assert warning == {"kind": "mixed_load", "message": ml.message([found("main.bronze.orders", 2, 3)], 1, 0),
                       "targets": [found("main.bronze.orders", 2, 3)], "count": 1, "append_count": 0}


def test_the_check_asks_for_the_lower_cased_targets_of_the_shared_rows_only_and_once():
    cursor = FakeCursor()
    rows = [shared("full", table="Orders"), shared("ct", table="orders", client="db2"), shared("ct", table="Lines"),
            shared("ct", table="dedicated", client=None)]
    main._mixed_load_warnings(cursor, rows)
    assert cursor.statements == [q.mixed_load_sinks_sql()]
    assert cursor.params == [(["main", "main"], ["bronze", "bronze"], ["lines", "orders"])]


def test_a_write_of_dedicated_tables_asks_nothing():
    cursor = FakeCursor()
    assert main._mixed_load_warnings(cursor, [shared("full", client=None)]) == []
    assert main._mixed_load_warnings(cursor, []) == []
    assert cursor.statements == []


def test_a_target_mixed_before_the_write_is_no_warning_and_the_others_still_are():
    cursor = FakeCursor([("main.bronze.a", 1, 1, 0), ("main.bronze.b", 1, 2, 1)])
    [warning] = main._mixed_load_warnings(cursor, [shared("full")], already_mixed={"main.bronze.a"})
    assert [t["target"] for t in warning["targets"]] == ["main.bronze.b"]
    assert "main.bronze.a" not in warning["message"] and warning["append_count"] == 1
    assert main._mixed_load_warnings(cursor, [shared("full")], already_mixed={"main.bronze.a", "main.bronze.b"}) == []


def test_no_mix_is_no_warning():
    assert main._mixed_load_warnings(FakeCursor([]), [shared("full")]) == []


def test_the_targets_mixed_before_a_write_are_the_names_the_check_finds():
    cursor = FakeCursor([("main.bronze.orders", 1, 1, 0)])
    before = main._checks_before(cursor, [shared("full")])
    assert before.mixed_load == {"main.bronze.orders"}


def test_a_write_earns_both_warnings_and_each_is_left_out_by_its_own_state_before(monkeypatch):
    answers = {q.split_databases_sql(): [("k1", "db1", ["g1", "g2"])], q.clash_jobs_sql(): [(7, "Customer")],
               q.mixed_load_sinks_sql(): [("main.bronze.orders", 1, 1, 0)]}

    class Both(FakeCursor):
        def execute(self, sql, params=()):
            super().execute(sql, params)
            self.answer = answers[sql]

    monkeypatch.setattr(q, "database_key", lambda row: row["src_database"])
    row = dict(shared("full"), src_database="db1")
    warnings = main._write_warnings(Both(), [row], main.WriteChecks(split=set(), mixed_load=set()))
    assert [w["kind"] for w in warnings] == ["split_database", "mixed_load"]
    assert main._write_warnings(Both(), [row], main.WriteChecks(split={"k1"}, mixed_load=set())) == [warnings[1]]
    assert main._write_warnings(Both(), [row], main.WriteChecks(split={"k1"}, mixed_load={"main.bronze.orders"})) == []


def test_the_groups_check_reads_the_targets_of_a_jobs_groups():
    cursor = FakeCursor([("main.bronze.orders", 1, 2, 1)])
    assert main._mixed_load_targets_of_groups(cursor, ("g1", "g2")) == [found("main.bronze.orders", 1, 2, True)]
    assert cursor.statements == [q.mixed_load_sinks_of_groups_sql()] and cursor.params == [(["g1", "g2"],)]


# ---- several transactions of one write ---------------------------------------------------------------

def test_the_warnings_of_a_write_in_several_transactions_are_merged_per_kind_and_the_counts_add():
    first = ml.warning([found("main.bronze.a"), found("main.bronze.b", append=True)])
    second = ml.warning([found("main.bronze.c"), found("main.bronze.d"), found("main.bronze.e")])
    split = main._split_warning([{"database": "db1", "groups": ["g1", "g2"]}], [])
    merged = main._merged_warnings([first, split, second])
    assert [w["kind"] for w in merged] == ["split_database", "mixed_load"]
    mixed = merged[1]
    assert mixed["count"] == 5 and mixed["append_count"] == 1
    assert [t["target"] for t in mixed["targets"]] == [f"main.bronze.{name}" for name in "abcde"]
    assert mixed["message"].startswith("5 targets are shared by tables with different load types: "
                                       "main.bronze.a, main.bronze.b, main.bronze.c and 2 more.")
    assert main._merged_warnings([]) == []


def test_a_long_list_of_targets_keeps_the_first_ones_and_the_whole_count():
    warning = ml.warning([found(f"main.bronze.t{n:04d}") for n in range(ml.LISTED + 50)])
    assert len(warning["targets"]) == ml.LISTED and warning["count"] == ml.LISTED + 50
    assert f"and {ml.LISTED + 47} more" in warning["message"]
    [again] = ml.merged([warning, ml.warning([found("main.bronze.z")])])
    assert again["count"] == ml.LISTED + 51 and f"and {ml.LISTED + 48} more" in again["message"]


def test_a_job_record_keeps_at_most_the_listed_targets_of_each_warning():
    warning = ml.warning([found(f"main.bronze.t{n:04d}") for n in range(ml.LISTED + 5)])
    [kept] = preparing.with_warnings({}, [warning])["warnings"]
    assert len(kept["targets"]) == preparing.LISTED and kept["count"] == ml.LISTED + 5 and "databases" not in kept
    split = main._split_warning([{"database": f"d{n}", "groups": ["g"]} for n in range(preparing.LISTED + 1)], [])
    assert len(preparing.with_warnings({}, [split])["warnings"][0]["databases"]) == preparing.LISTED


# ---- the preview before Create -----------------------------------------------------------------------

HEAD = "src_connection,src_database,src_schema,src_table,select_list,primary_key,load_type\n"
FAN_IN = {"fan_in": True, "sink_catalog": "main", "sink_schema": "bronze", "sink_table": "{table}",
          "identity": {"engagement": "{database}"}}


def planned(text, settings=FAN_IN):
    return nj.plan("Job", HEAD + text, settings, 4, new_job=True)


def test_a_preview_warns_of_a_target_its_own_tables_would_mix():
    plan = planned('sql_a,eng_001,gen,t1,"id",id,ct\nsql_a,eng_002,gen,t1,"id",id,full\n'
                   'sql_a,eng_001,gen,t2,"id",id,ct\nsql_a,eng_002,gen,t2,"id",id,ct\n')
    [warning] = nj.summary(plan)["warnings"]
    assert warning["kind"] == "mixed_load" and [t["target"] for t in warning["targets"]] == ["main.bronze.t1"]
    assert warning["message"] == ml.message([found("main.bronze.t1", full=1, other=1)], 1, 0)


def test_a_preview_has_no_warning_for_change_tracked_with_cursor_all_full_or_dedicated_targets():
    cursor = 'sql_a,eng_001,gen,t1,"id",id,ct\nsql_a,eng_002,gen,t1,"id",id,cursor\n'
    assert nj.summary(planned(cursor))["warnings"] == []
    assert nj.summary(planned('sql_a,eng_001,gen,t1,"id",id,full\nsql_a,eng_002,gen,t1,"id",id,full\n'))["warnings"] == []
    dedicated = {"sink_catalog": "main", "sink_schema": "bronze", "sink_table": "{database}_{table}"}
    mixed_types = 'sql_a,eng_001,gen,t1,"id",id,ct\nsql_a,eng_002,gen,t1,"id",id,full\n'
    assert nj.summary(planned(mixed_types, dedicated))["warnings"] == []


def test_a_preview_leaves_out_lines_that_cannot_be_written():
    plan = planned('sql_a,eng_001,gen,t1,"id",id,ct\nsql_a,eng_002,gen,t1,"id",id,bogus\n')
    assert nj.summary(plan)["warnings"] == []


# ---- placement ---------------------------------------------------------------------------------------

def test_new_ingestion_job_shows_the_previews_warning_and_a_saved_edit_shows_its_own():
    new_job = (FRONTEND / "pages" / "NewJob.tsx").read_text()
    assert "<WriteWarnings warnings={summary?.warnings} />" in new_job
    assert new_job.index("<WriteWarnings warnings={summary?.warnings} />") < new_job.index('<Section title="What it ingests"')
    edit = (FRONTEND / "pages" / "JobEdit.tsx").read_text()
    assert "<WriteWarnings warnings={save.data?.settings?.warnings} />" in edit


def test_the_job_edit_answer_carries_the_warnings_of_the_edit():
    source = Path(main.__file__).read_text()
    endpoint = source[source.index("def edit_ingestion_job_settings("):source.index("def _snapshot(")]
    assert endpoint.index("mixed_before = ") < endpoint.index("jss.apply(") < endpoint.index('found["warnings"] = ')
