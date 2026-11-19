"""The reason a failing table failed, as the job page shows it first: the source's own words (queries.source_reason),
not Spark's wrapper, in the Failing tables section and in the job's failures by cause, which the job page now shows as
the jobs list builds them. Plain functions and source checks of the page; no database, no Spark, no app run."""
from pathlib import Path

import pytest

import queries as q

PAGES = Path(__file__).resolve().parents[1] / "src" / "frontend" / "src" / "pages"
STYLES = (PAGES.parent / "styles.css").read_text()
# The error the engine recorded for every table of the job that read a database its login couldn't read: Spark's
# wrapper first, then SQL Server's reason, then the Java stack.
DRIVER = "com.microsoft.sqlserver.jdbc.SQLServerException"
PERMISSION = (f"An error occurred while calling o553.sql. : {DRIVER}: The SELECT permission was denied on the object "
              "'customers', database 'lfc_test', schema 'dbo'.\n"
              f"\tat {DRIVER}.makeFromDatabaseError(SQLServerException.java:265)\n"
              "\tat org.apache.spark.sql.execution.datasources.jdbc.JDBCRDD$.getQueryOutputSchema(JDBCRDD.scala:68)\n")
POOL_LIMIT = (f"An error occurred while calling o912.sql. : {DRIVER}: Resource ID : 1. The request limit for the "
              "elastic pool is 200 and has been reached. See 'http://go.microsoft.com/fwlink/?LinkId=267637' for "
              "assistance.\n\tat com.microsoft.sqlserver.jdbc.X(X.java:1)")


@pytest.mark.parametrize("error, reason", [
    (PERMISSION, "The SELECT permission was denied on the object 'customers', database 'lfc_test', schema 'dbo'."),
    (POOL_LIMIT, "Resource ID : 1. The request limit for the elastic pool is 200 and has been reached. See "
                 "'http://go.microsoft.com/fwlink/?LinkId=267637' for assistance."),
    # The engine adds the deepest Java cause; the driver's message there is the reason.
    ("[FAILED_JDBC.CONNECTION] Failed JDBC jdbc: on the operation: Connection | caused by "
     f"{DRIVER}: Login failed for user 'lfc_discover'. ClientConnectionId:3c1d-9a",
     "Login failed for user 'lfc_discover'."),
    # The deepest driver exception wins over an outer one and over a non-driver cause.
    ("py4j.protocol.Py4JJavaError: An error occurred while calling o1.sql. : java.sql.SQLException: outer\n"
     "Caused by: com.microsoft.sqlserver.jdbc.SQLServerException: Cannot open database \"x\" requested by the login.\n"
     "Caused by: java.lang.RuntimeException: wrapped", "Cannot open database \"x\" requested by the login."),
    # No Java exception at all: the first line, without Spark's wrapper.
    ("An error occurred while calling o5.sql. : the target table is missing\nmore", "the target table is missing"),
    ("planner: table has no primary key", "planner: table has no primary key"),
    ("", ""), (None, ""),
])
def test_the_reason_is_the_sources_own_words(error, reason):
    assert q.source_reason(error) == reason


def test_a_reason_is_one_line_and_bounded():
    reason = q.source_reason("com.microsoft.sqlserver.jdbc.SQLServerException: " + "x" * 3000)
    assert len(reason) == q.REASON_CHARS and "\n" not in reason


def test_each_failing_table_carries_its_reason_beside_the_whole_message():
    [row] = q.failing_table_rows([{"control_id": 1, "task_collection": "g", "server": "s", "src_database": "lfc_test",
                                   "src_schema": "dbo", "src_table": "customers", "error": PERMISSION, "runs": 3,
                                   "failures": 3}])
    assert row["reason"].startswith("The SELECT permission was denied on the object 'customers'")
    assert row["message"] == PERMISSION


def test_each_cause_carries_the_reason_of_its_example():
    rows = [{"control_id": n, "task_collection": "g", "server": "s", "src_database": "lfc_test", "src_schema": "dbo",
             "src_table": f"t{n}", "error": PERMISSION.replace("customers", f"t{n}")} for n in range(3)]
    [cause] = q.group_errors(rows)
    assert cause["tables"] == 3 and cause["label"] == "Permission denied at the source or sink"
    assert cause["reason"].startswith("The SELECT permission was denied on the object 't0'")


def test_the_failing_tables_section_shows_the_reason_first_and_the_message_on_hover_and_copy():
    page = (PAGES / "FailingTables.tsx").read_text()
    assert "SHOWN_MESSAGE" not in page and ".slice(0, " not in page
    assert '<Tip text={row.message} focusable>{row.reason || row.message}</Tip>' in page
    assert '<CopyButton text={row.message} label="Copy the full message" />' in page
    assert 'className="small reason"' in page
    assert ".attention-row .reason, table.data.one-line td.reason { white-space: normal; }" in STYLES


def test_the_job_page_shows_the_jobs_failures_by_cause():
    page = (PAGES / "FailingTables.tsx").read_text()
    job_page = (PAGES / "IngestionJob.tsx").read_text()
    assert "<FailingTables jobId={id} causes={j.causes}" in job_page
    assert "{(causes ?? []).length > 0 && <FailureCauses causes={causes!} />}" in page
    assert "<Tip text={c.example} focusable>{c.reason || c.example}</Tip>" in page


def test_the_job_endpoint_takes_its_causes_from_the_jobs_list(monkeypatch):
    import main
    causes = [{"label": "Permission denied at the source or sink", "reason": "denied"}]
    monkeypatch.setattr(main, "_jobs_view", lambda: {"jobs": [{"id": 52, "causes": causes}, {"id": 53, "causes": []}]})
    assert main._job_causes(52) == causes
    assert main._job_causes(53) == [] and main._job_causes(54) == []
