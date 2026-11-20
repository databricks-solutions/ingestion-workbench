"""Where a full reload in append mode is chosen, the page says what it does: each run adds a whole copy of the table,
so the target grows (components.FullAppendNote). The job form (New ingestion job and Edit), Add table and Import CSV
all show it. Read from the source, as the other frontend checks are."""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "frontend" / "src"


def test_the_note_says_what_append_does_to_a_full_reload_and_only_for_that_pair():
    components = (SRC / "components.tsx").read_text()
    at = components.index("export function FullAppendNote(")
    note = components[at:components.index("\n}\n", at)]
    assert 'if (loadType !== "full" || writeMode !== "append") return null;' in note
    assert "whole copy of each table every run" in note and "whole copy of the table every run" in note
    assert note.count("Merge replaces the rows instead.") == 2


def test_the_job_form_add_table_and_import_csv_show_it_where_the_pair_is_chosen():
    pages = SRC / "pages"
    assert '<FullAppendNote loadType="full" writeMode={w.write_mode_full} many />' in (pages / "jobForm.tsx").read_text()
    assert "<FullAppendNote loadType={row.load_type} writeMode={row.write_mode}" in (pages / "AddTable.tsx").read_text()
    assert '<FullAppendNote loadType={shown("load_type")} writeMode={shown("write_mode")} many' in (
        pages / "ImportCsv.tsx").read_text()
