"""New ingestion job's How it writes (NewJob.tsx, jobForm.tsx): the page opens at the server's defaults
(new_job.DEFAULTS), Fan-in "Yes: shared tables" and Change-tracked tables "Append: change log", each a choice already
made as the Jobs Create job form preselects its fields, with the other option still offered. Edit shows a job's own
stored values. Plain source checks, no app run."""
from pathlib import Path

import new_job as nj

PAGES = Path(__file__).resolve().parents[1] / "src" / "frontend" / "src" / "pages"
NEW_JOB = (PAGES / "NewJob.tsx").read_text()
FORM = (PAGES / "jobForm.tsx").read_text()
EDIT = (PAGES / "JobEdit.tsx").read_text()


def test_the_page_opens_at_the_servers_defaults():
    start = NEW_JOB[NEW_JOB.index("const [w, setW] = useState<Row>({"):NEW_JOB.index("const [identity, setIdentity]")]
    assert 'fan_in: true, write_mode_changes: "append", write_mode_full: "merge"' in start
    assert (nj.DEFAULTS["fan_in"], nj.DEFAULTS["write_mode_changes"], nj.DEFAULTS["write_mode_full"]) == (
        True, "append", "merge")


def test_merge_and_own_tables_can_still_be_chosen():
    assert '<option value="true">Yes: shared tables</option><option value="false">No: own tables</option>' in FORM
    assert '<option value="append">Append: change log</option><option value="merge">Merge: current state</option>' in FORM


def test_edit_shows_the_jobs_stored_values_not_the_new_job_defaults():
    assert "const form = { fan_in: value(s.fan_in), write_mode_changes: value(s.write_mode_changes)" in EDIT
