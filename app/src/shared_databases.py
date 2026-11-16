"""When one source database may be read by a second ingestion job.

A database may be in a second live job only when the two jobs write to different targets: no catalog.schema that one
job's tables write to is one the other job's tables write to. Each job then reads the database on its own schedule,
with its own groups, checkpoints and first loads, and the first job is not touched. When the two jobs would share a
catalog.schema, the write is refused, naming the other job. A database is the same database in two rows when both
reach the same server and have the same name (server_key): a row read by host is at its host and port, and a row read
through a Unity Catalog connection is at the host and port the connection names, so two connections to one server, or
a connection and a host, are one server. The login is not part of it: two logins to one database read one database.

Every write that gives a job a database another live job reads asks the user to confirm first (Create, Add tables, Add
databases, an edit, a registration, an import, a group added to a job, a Restore): the request is refused with the
list (confirmation) until it is sent again with confirm_shared. Its answer then also warns about the split database,
as any split does (main._split_database_warnings).

Pure functions: main.py gathers the rows and the Lakebase reads, and these decide and word the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

import job_settings as js
import queries as q

# The tags a target catalog or schema can be filled from before any table is read: a Create checks a chosen database
# against the other jobs from its settings alone, so a template that names the source schema or table is known only
# once the tables are, and is checked at the write instead.
KNOWN_BEFORE_TABLES = ("{server}", "{database}")
TABLE_TAGS = ("{schema}", "{table}")

CONFIRM_SHARED = ("some of these databases are already read by another ingestion job: confirm to read them from this "
                  "job too, into its own catalog and schema, or leave them out")


def server_of_connection(connection: dict) -> Optional[str]:
    """The server a Unity Catalog connection names, as host:port in lower case (the source type's default port when it
    names none), or None when it names no host."""
    host = _trimmed(connection.get("host"))
    if not host:
        return None
    kind = _trimmed(connection.get("connection_type")) or "sqlserver"
    port = str(connection.get("port") or "").strip()
    return f"{host}:{int(port) if port.isdigit() and int(port) else q.DEFAULT_PORTS.get(kind, 0)}"


def _trimmed(value) -> str:
    """A name as queries.server_key_sql compares it: spaces trimmed (as btrim does, no other whitespace), lower case."""
    return str(value or "").strip(" ").lower()


def connections_named(rows) -> Set[str]:
    """The connections some rows read through (no host), as server_key looks them up."""
    return {_trimmed(row.get("src_connection")) for row in rows
            if not _trimmed(row.get("src_host")) and _trimmed(row.get("src_connection"))}


def server_key(row: dict, endpoints: Dict[str, str]) -> str:
    """A row's database for this rule: its server and its name, as one text (queries.server_key_sql in SQL). A row with
    a host is at host:port (a blank port is the source type's default); a row read through a connection is at the
    server endpoints gives for the connection's lower-case name, or at connection:<name> when the app can't see it."""
    host = _trimmed(row.get("src_host"))
    if host:
        kind = _trimmed(row.get("src_type")) or "sqlserver"
        server = f"{host}:{int(row.get('src_port') or 0) or q.DEFAULT_PORTS.get(kind, 0)}"
    else:
        connection = _trimmed(row.get("src_connection"))
        server = endpoints.get(connection) or f"connection:{connection}"
    return f"{server}\x1f{row.get('src_database') or ''}"


def target_of(row: dict) -> Optional[str]:
    """The catalog.schema a control row writes to, lower case, or None when either part is blank or still a
    template."""
    catalog = str(row.get("sink_catalog") or "").strip().lower()
    schema = str(row.get("sink_schema") or "").strip().lower()
    if not catalog or not schema or "{" in catalog or "{" in schema:
        return None
    return f"{catalog}.{schema}"


def planned_target(settings: dict, database_row: dict, unity_catalog: bool = True) -> Optional[str]:
    """The catalog.schema a new job's settings would give one chosen database's tables, filled from what is known
    before its tables are read (its server and name). None when a template needs a table's own schema or name."""
    parts = []
    for name in ("sink_catalog", "sink_schema"):
        template = str(settings.get(name) or "").strip()
        if not template or any(tag in template for tag in TABLE_TAGS):
            return None
        parts.append(js.render(template, database_row, target=True, unity_catalog=unity_catalog))
    return target_of({"sink_catalog": parts[0], "sink_schema": parts[1]})


@dataclass(slots=True, kw_only=True)
class Owner:
    """A live job that reads one of a write's databases: the database's key (queries.database_key) and its name as
    written, and the job."""
    key: str
    database: str
    job_id: int
    job: str


@dataclass(slots=True, kw_only=True)
class Clash:
    """A database another live job reads, where that job also writes to a catalog.schema this write's job does."""
    database: str
    job_id: int
    job: str
    target: str

    def message(self) -> str:
        return (f"database {self.database} is also in the ingestion job {self.job}, which writes to {self.target} too: "
                "a second job may read a database only into a different catalog or schema, so give this job another "
                "target catalog or schema, leave the database out, or add its tables to that job")


@dataclass(slots=True, kw_only=True)
class Share:
    """A database another live job reads, into other targets: a second job may read it too, once confirmed.
    unchecked: this job's target for it still needs a table's schema or name, so it is checked at the write."""
    database: str
    job_id: int
    job: str
    unchecked: bool = False

    def as_dict(self) -> dict:
        return {"database": self.database, "job_id": self.job_id, "job": self.job, "unchecked": self.unchecked}


@dataclass(slots=True, kw_only=True)
class Verdict:
    """What the rule says about a write before it is made: the clashes that refuse it, and the databases it would
    share with another live job, which a Create asks to confirm."""
    clashes: List[Clash] = field(default_factory=list)
    shares: List[Share] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"refused": [clash.message() for clash in self.clashes],
                "jobs": jobs_of(self.clashes),
                "shares": [share.as_dict() for share in self.shares]}


def judge(owners: Iterable[Owner], owner_targets: Dict[int, Set[str]],
          planned: Dict[str, Set[Optional[str]]]) -> Verdict:
    """The rule over a new job's databases before it is made.

    owners: the live jobs that read each database now, one per database and job (queries.live_database_owners_sql).
    owner_targets: which of the new job's planned targets each of those jobs writes (queries.jobs_writing_targets_sql).
    planned: for each database, by its key, the targets the new job would give its tables; None among them means a
      target not known yet (a template that needs a table's schema or name).
    The rule is the whole job's: a database another job reads is a clash when any target of the new job, for any of
    its databases, is one that job writes; else it is a share, unchecked while some target is not known yet."""
    verdict = Verdict()
    every = {target for targets in planned.values() for target in targets if target}
    unknown = any(None in targets for targets in planned.values())
    for owner in owners:
        common = sorted(every & owner_targets.get(owner.job_id, set()))
        if common:
            verdict.clashes.append(Clash(database=owner.database, job_id=owner.job_id, job=owner.job, target=common[0]))
        else:
            verdict.shares.append(Share(database=owner.database, job_id=owner.job_id, job=owner.job, unchecked=unknown))
    return verdict


def jobs_of(items: Iterable) -> List[dict]:
    """The jobs some clashes or shares name, once each, in the order first named: what a refusal or a confirmation
    links to."""
    jobs: Dict[int, dict] = {}
    for item in items:
        jobs.setdefault(item.job_id, {"id": item.job_id, "name": item.job})
    return list(jobs.values())


def refusal(clashes: List[Clash]) -> dict:
    """A 409's detail for clashes: the first clash in words (with a count of the rest), and the jobs to link to."""
    message = clashes[0].message()
    if len(clashes) > 1:
        message += f" ({len(clashes) - 1} more {'clash' if len(clashes) == 2 else 'clashes'} like it)"
    return {"message": message, "jobs": jobs_of(clashes), "reason": "shared_target"}


def confirmation(shares: List[Share]) -> dict:
    """A 409's detail for a Create that would share databases and was not confirmed: the databases with their jobs,
    and confirm_shared, which the page reads to ask the user and send the Create again."""
    return {"message": CONFIRM_SHARED, "shares": [share.as_dict() for share in shares], "jobs": jobs_of(shares),
            "confirm_shared": True}
