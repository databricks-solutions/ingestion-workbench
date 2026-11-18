"""The bundle's reads_cap: one variable (8 by default) passed to the app as READS_CAP, which warehouse_reads reads
within bounds. Read as text, like the other bundle tests."""
from pathlib import Path

import warehouse_reads as wr

REPO = Path(__file__).resolve().parents[2]
VARIABLES = (REPO / "databricks.yml").read_text()
RESOURCES = (REPO / "app" / "resources" / "lakefed_ingest_app.yml").read_text()


def _block(name):
    """A variable's lines, up to its default."""
    start = VARIABLES.index(f"\n  {name}:\n")
    return VARIABLES[start:VARIABLES.index("\n", VARIABLES.index("    default:", start)) + 1]


def test_the_variable_exists_with_the_cap_as_its_default_and_the_old_two_are_gone():
    assert _block("reads_cap").endswith("    default: 8\n")
    assert "Never below 4" in " ".join(_block("reads_cap").split())
    assert "reads_start_cap" not in VARIABLES and "reads_max_cap" not in VARIABLES


def test_the_app_is_told_it_and_nothing_else():
    assert "          - name: READS_CAP\n            value: ${var.reads_cap}\n" in RESOURCES
    assert "READS_START_CAP" not in RESOURCES and "READS_MAX_CAP" not in RESOURCES


def test_the_setting_is_read_within_bounds(monkeypatch):
    monkeypatch.setenv("READS_CAP", "12")
    assert wr._setting("READS_CAP", 8, wr.MIN_CAP, wr.MAX_CAP) == 12
    monkeypatch.setenv("READS_CAP", "1")
    assert wr._setting("READS_CAP", 8, wr.MIN_CAP, wr.MAX_CAP) == wr.MIN_CAP == 4
    monkeypatch.setenv("READS_CAP", "999")
    assert wr._setting("READS_CAP", 8, wr.MIN_CAP, wr.MAX_CAP) == wr.MAX_CAP == 256
    monkeypatch.setenv("READS_CAP", "eight")
    assert wr._setting("READS_CAP", 8, wr.MIN_CAP, wr.MAX_CAP) == 8
    monkeypatch.setenv("READS_CAP", "")
    assert wr._setting("READS_CAP", 8, wr.MIN_CAP, wr.MAX_CAP) == 8


def test_the_cap_is_8_unless_a_setting_names_another(monkeypatch):
    monkeypatch.delenv("READS_CAP", raising=False)
    assert wr.cap_from_environment() == 8
    monkeypatch.setenv("READS_CAP", "6")
    assert wr.cap_from_environment() == 6


def test_the_old_reads_start_cap_is_not_read(monkeypatch):
    """Every app this bundle deploys is told READS_CAP, so the old name has nothing left to stand in for."""
    monkeypatch.delenv("READS_CAP", raising=False)
    monkeypatch.setenv("READS_START_CAP", "12")
    assert wr.cap_from_environment() == 8
