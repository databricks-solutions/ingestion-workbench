// The tick states of the Chosen pickers' top rows (SourcePick and Discover). Kept free of React so a test can run it
// with node (tests/frontend_pick_test.py).

export type Pick = "all" | "part" | "none";

/** Whether a database's name contains a filter, in any case: the rule a whole-server choice covers databases by. */
export const underFilter = (database: unknown, filter: string) => String(database).toLowerCase().includes(filter.toLowerCase());

/** The SQL Server LIKE pattern for the database names containing a filter, as underFilter matches them, so reading a
 *  whole server under a filter reads only those databases. Its wildcards (_, % and [) are taken literally. Null when
 *  there is no filter, or when it has a character the server's listing doesn't take: the whole server is read then. */
export function likeContaining(filter: string): string | null {
  if (!filter) return null;
  const pattern = `%${filter.replace(/\[/g, "[[]").replace(/_/g, "[_]").replace(/%/g, "[%]")}%`;
  return /^[A-Za-z0-9_%\-\[\].$#@]{1,128}$/.test(pattern) ? pattern : null;
}

/** The whole server, as Discover chooses it: on, the filter it was ticked under (it covers the databases whose names
 *  contain it), and the databases unticked under it since (excluded): it takes every one it covers but those. */
export type WholeServer = { on: boolean; filter: string; excluded: string[] };
export const noWholeServer: WholeServer = { on: false, filter: "", excluded: [] };

const sameName = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
/** Whether a database was unticked under the whole server. */
export const excludedFrom = (whole: WholeServer, database: string) => whole.excluded.some((name) => sameName(name, database));
/** Whether the whole server takes a database: under its filter, and not unticked since. */
export const coveredByServer = (whole: WholeServer, database: string) =>
  whole.on && underFilter(database, whole.filter) && !excludedFrom(whole, database);

/** How the whole server stands over the databases a page shows (those under the page's filter): whether its filter
 *  covers them all, and which of them were unticked under it. */
function overPage(whole: { on: boolean; filter: string; excluded?: string[] }, pageFilter: string) {
  return { covers: whole.on && (whole.filter === "" || underFilter(pageFilter, whole.filter)),
           untickedShown: (whole.excluded ?? []).filter((name) => underFilter(name, pageFilter)) };
}

/**
 * The server row's tick. It is ticked when the whole server is chosen under a filter that covers every database the page
 * shows (an empty filter covers them all; a filter covers the databases whose names contain it) and none of those was
 * unticked. It is in part when anything else is chosen, and empty when nothing is. (SourcePick's server has no
 * databases unticked under it, so it passes none.)
 */
export function serverRowPick(whole: Omit<WholeServer, "excluded"> & { excluded?: string[] }, pageFilter: string,
                              anythingElseChosen: boolean): Pick {
  const { covers, untickedShown } = overPage(whole, pageFilter);
  if (covers && !untickedShown.length) return "all";
  return whole.on || anythingElseChosen ? "part" : "none";
}

/**
 * The whole server after a click on its row, as the Jobs list's and Catalog Explorer's select-all box answers one: a
 * row ticked in full is unticked; one in part because databases the page shows were unticked under the whole server
 * gets them back, the rest of the choice kept; anything else ticks every database under the page's filter, none
 * unticked.
 */
export function serverTick(whole: WholeServer, pageFilter: string, pick: Pick): WholeServer {
  if (pick === "all") return { on: false, filter: pageFilter, excluded: [] };
  const { covers, untickedShown } = overPage(whole, pageFilter);
  if (covers && untickedShown.length) return takeBack(whole, untickedShown);
  return { on: true, filter: pageFilter, excluded: [] };
}

/** The whole server with these databases, unticked under it before, ticked again. */
export const takeBack = (whole: WholeServer, names: string[]): WholeServer =>
  ({ ...whole, excluded: whole.excluded.filter((name) => !names.some((back) => sameName(back, name))) });

/** The whole server after a click on a database under it: one it takes is unticked (left out, the server stays chosen
 *  in part); one unticked before is ticked again. Null for a database the whole server isn't over: its own tick
 *  chooses it. */
export function databaseTickUnderServer(whole: WholeServer, database: string): WholeServer | null {
  if (!whole.on || !underFilter(database, whole.filter)) return null;
  return excludedFrom(whole, database) ? takeBack(whole, [database]) : { ...whole, excluded: [...whole.excluded, database] };
}

/** What a database's check said: the login can open it, can't (it is greyed out and can't be chosen), or not known
 *  yet (still being checked, or listed without a check). */
export type Access = "open" | "closed" | "unknown";
export const accessOf = (row: { open?: boolean | null } | undefined): Access =>
  row?.open === true ? "open" : row?.open === false ? "closed" : "unknown";

/** The databases of these that may be read: every one but those the login can't open, which nothing reads (their
 *  rows are greyed out, their chevron is off and a whole-database choice leaves them out). */
export const readable = (names: string[], seen: Record<string, { open?: boolean | null } | undefined>): string[] =>
  names.filter((name) => accessOf(seen[name]) !== "closed");

/** The whole server's part of a choice: the databases its filter keeps, less those the login can't open; their
 *  tables as counted so far, and how many of them aren't checked yet; and the tables of each database unticked under
 *  it (null: not counted yet), which it doesn't take. */
export type ServerCount = { matched: number; closed: number; unknown: number; tables: number; excluded: (number | null)[] };

const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);

/** How much a choice takes, counted as the step shows it at its top: databases chosen (whole or in part) and their
 *  tables. A whole database's tables are its check's count; one not counted yet makes the tables "counting". */
export function selectionCounts(server: ServerCount | null, wholeTables: (number | null)[], pickedTables: number[]):
    { databases: number; tables: number; counting: boolean; leftOut: number } {
  const excluded = server?.excluded ?? [];
  const serverDatabases = server ? Math.max(0, server.matched - server.closed - excluded.length) : 0;
  const known = wholeTables.filter((n): n is number => n != null);
  const excludedKnown = excluded.filter((n): n is number => n != null);
  return {
    databases: serverDatabases + wholeTables.length + pickedTables.length,
    tables: Math.max(0, (server?.tables ?? 0) - sum(excludedKnown)) + sum(known) + sum(pickedTables),
    counting: Boolean(server?.unknown) || known.length < wholeTables.length || excludedKnown.length < excluded.length,
    leftOut: server?.closed ?? 0,
  };
}

const n = (value: number) => value.toLocaleString("en-US");

/** "Chosen: 3 of 201 databases, 3,000 tables", or just the databases while some tables aren't counted yet (they are
 *  counted out of sight, with no spinner, while the user chooses). */
export function countsText(counts: { databases: number; tables: number; counting: boolean }, listed: number): string {
  const tables = counts.counting ? null : `${n(counts.tables)} ${counts.tables === 1 ? "table" : "tables"}`;
  return `Chosen: ${n(counts.databases)} of ${n(listed)} ${listed === 1 ? "database" : "databases"}${tables ? `, ${tables}` : ""}`;
}
