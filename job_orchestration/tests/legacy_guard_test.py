"""The job orchestration bundle is being retired: until it is removed on 15 January 2027, every bundle command refuses
unless legacy_migrating is true. The check is the bundle's own postinit script, which the Databricks CLI runs once it
has resolved the variables and lookups, and before validate, deploy, run or destroy does its work. These tests run
that script as the CLI does (bash -ec), with the variable put in as the CLI interpolates it, so no workspace is
needed. That the CLI fills in ${var.legacy_migrating} in the script is checked against a workspace by hand (the pull
request's testing); if a CLI stopped doing so, bash would fail on the literal and every command would refuse."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

BUNDLE_DIR = Path(__file__).resolve().parents[1]
BUNDLE = yaml.safe_load((BUNDLE_DIR / "databricks.yml").read_text())
SCRIPT = BUNDLE["experimental"]["scripts"]["postinit"]
REPO = BUNDLE_DIR.parent
NOTE_ANCHOR = "moving-off-the-job-orchestration-version"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="the CLI runs bundle scripts with bash")


def _run_guard(value: str) -> subprocess.CompletedProcess:
    """The postinit script with ${var.legacy_migrating} replaced by the value, run with bash."""
    script = SCRIPT.replace("${var.legacy_migrating}", value)
    return subprocess.run(["bash", "-ec", script], capture_output=True, text=True, timeout=30)


def test_the_setting_is_off_unless_given():
    assert BUNDLE["variables"]["legacy_migrating"]["default"] == "false"


def test_the_check_runs_after_the_variables_are_resolved():
    """preinit would see the literal ${var.legacy_migrating} (the CLI resolves variables after it), so the check must
    be postinit, which every bundle command runs."""
    assert set(BUNDLE["experimental"]["scripts"]) == {"postinit"}
    assert "${var.legacy_migrating}" in SCRIPT


@needs_bash
@pytest.mark.parametrize("value", ["false", "", "True", "yes", "1"])
def test_a_command_without_the_setting_refuses_naming_the_date_and_the_migration_note(value):
    result = _run_guard(value)
    assert result.returncode != 0
    assert "15 January 2027" in result.stderr
    assert f"docs/deploy.md#{NOTE_ANCHOR}" in result.stderr
    assert "--var legacy_migrating=true" in result.stderr


@needs_bash
def test_a_command_with_the_setting_goes_ahead_silently():
    result = _run_guard("true")
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""


def test_the_migration_note_it_names_exists():
    deploy_guide = (REPO / "docs" / "deploy.md").read_text()
    assert "\n### Moving off the job orchestration version\n" in deploy_guide
    assert "15 January 2027" in deploy_guide


def test_the_default_version_has_no_such_check():
    root_config = (REPO / "databricks.yml").read_text()
    assert "legacy_migrating" not in root_config
