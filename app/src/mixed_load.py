"""The warning for a shared target whose tables load in different ways.

A target is shared when its control rows carry a client_id: each row writes only its own slice, marked by the
__client_id__ column. When one slice is a full reload and another is change-tracked or cursor, the data stays correct
(a full reload replaces only its own slice), but every run of the full reload table deletes and rewrites its slice
even when nothing changed, so the target's change feed, and any stream reading it, gets those rows again each run.

Nothing here refuses a write. The app tells the person who made the change, where they made it, in the yellow alert
the Databricks UI uses for a warning that blocks nothing (components.tsx WriteWarnings). This module is plain Python
with no database: main.py asks Lakebase which targets are mixed (queries.mixed_load_sinks_sql) and this builds the
warning from the answer, and from the planned rows of a Create before anything is written (preview_warnings).
"""
from typing import Collection, Dict, Iterable, List, Optional

KIND = "mixed_load"
FULL = "full"
# How many targets one warning lists: a record that keeps the warning is read by every page of jobs, and the message
# counts them all.
LISTED = 200

_SINK_PARTS = ("sink_catalog", "sink_schema", "sink_table")
_REWRITES = ("Each run of a full reload table deletes and rewrites all of its rows in the target, even when nothing "
             "changed, so the target's change feed and any stream reading it get those rows again every run.")
_APPENDS = ("Each run of a full reload table appends all of its rows to the target again, even when nothing changed, "
            "so the target grows by those rows every run.")
_BOTH = f"{_REWRITES} A target in append mode takes those rows again instead: it grows by them every run."
_ADVICE = ("The data stays correct. To avoid it, give the full reload tables a target of their own, or turn on Change "
           "Tracking for them at the source and read the tables again.")


def target_of(row: dict) -> str:
    """The target a control row writes as catalog.schema.table in lower case (Unity Catalog names are not case
    sensitive), or "" when any part is blank, as in a preview whose target is not typed yet."""
    parts = [str(row.get(key) or "").strip().lower() for key in _SINK_PARTS]
    return ".".join(parts) if all(parts) else ""


def mixed_targets(rows: Iterable[dict]) -> List[dict]:
    """The targets that the given control rows alone would mix, by name: each as {target, full, other, append}, where
    full and other count the shared rows by load type and append is whether a full reload row writes in append mode.
    Dedicated rows (no client_id) are left out: a dedicated target has exactly one row."""
    counts: Dict[str, dict] = {}
    for row in rows:
        target = target_of(row)
        if not target or not str(row.get("client_id") or "").strip():
            continue
        found = counts.setdefault(target, {"target": target, "full": 0, "other": 0, "append": False})
        if row.get("load_type") == FULL:
            found["full"] += 1
            found["append"] = found["append"] or row.get("write_mode") == "append"
        else:
            found["other"] += 1
    return [found for _, found in sorted(counts.items()) if found["full"] and found["other"]]


def warnings(targets: List[dict], already_mixed: Collection[str] = frozenset()) -> List[dict]:
    """The warning for the targets that are mixed now and were not before the write (already_mixed, their names), as a
    list of at most one. A later write to a target that was mixed already says nothing."""
    new = [found for found in targets if found["target"] not in already_mixed]
    return [warning(new)] if new else []


def preview_warnings(lines: Iterable[dict]) -> List[dict]:
    """The same warning for a Create's planned lines (new_job.plan), before anything is written: the targets that its
    own shared tables would mix. It reads nothing, so a mix with rows that other jobs already hold is found only
    after the write."""
    return warnings(mixed_targets(line["row"] for line in lines if line["status"] == "valid"))


def warning(targets: List[dict], count: Optional[int] = None, append_count: Optional[int] = None) -> dict:
    """The warning: its text, the first LISTED targets with their counts, how many targets there are in all and how
    many of those a full reload table appends to. count and append_count are given when merging warnings whose
    target lists were cut."""
    ordered = sorted(targets, key=lambda found: found["target"])
    count = len(ordered) if count is None else count
    append_count = sum(bool(found["append"]) for found in ordered) if append_count is None else append_count
    return {"kind": KIND, "message": message(ordered, count, append_count), "targets": ordered[:LISTED],
            "count": count, "append_count": append_count}


def merged(found: List[dict]) -> List[dict]:
    """The mixed-load warnings among several (the transactions of one large write, one chunk of databases each) as one
    warning. A target is warned about once: after the chunk that mixed it, it is mixed before the next chunk too, so
    the warnings name different targets and their counts add."""
    own = [item for item in found if item.get("kind") == KIND]
    if not own:
        return []
    return [warning([target for item in own for target in item.get("targets", [])],
                    sum(int(item.get("count") or 0) for item in own),
                    sum(int(item.get("append_count") or 0) for item in own))]


def message(targets: List[dict], count: int, append_count: int) -> str:
    """The text: the one target with its full reload and other table counts, or the first three targets and a count of
    the rest, then what a full reload table costs a shared target and what to do about it. Only names already read
    are used. The wording follows the Databricks UI's warnings: what is true, what it costs, and the way out."""
    if count == 1 and targets:
        found = targets[0]
        head = (f"Target {found['target']} is shared by tables with different load types: {found['full']} full "
                f"reload and {found['other']} change-tracked or cursor.")
    else:
        names = [found["target"] for found in targets[:3]]
        shown = ", ".join(names) + (f" and {count - len(names)} more" if count > len(names) else "")
        head = f"{count} targets are shared by tables with different load types: {shown}."
    cost = _REWRITES if append_count == 0 else _APPENDS if append_count >= count else _BOTH
    return f"{head} {cost} {_ADVICE}"
