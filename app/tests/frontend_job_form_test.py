"""The job form (New ingestion job and Edit) says what append does to full-reload tables when it is chosen: each run
adds a whole copy of the table, so the target grows. Read from the source, as the other frontend checks are."""
from pathlib import Path

FORM = (Path(__file__).resolve().parents[1] / "src" / "frontend" / "src" / "pages" / "jobForm.tsx").read_text()


def test_the_job_form_warns_when_full_reload_tables_append():
    at = FORM.index('w.write_mode_full === "append" &&')
    note = FORM[at:FORM.index("</p>}", at)]
    assert 'className="impact"' in note and "whole copy of each" in note and "Merge replaces the rows instead" in note
