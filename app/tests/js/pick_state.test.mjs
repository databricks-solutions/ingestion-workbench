// Tests for the pickers' pure helpers (app/src/frontend/src/pages/pickState.ts). Run with `node --test`;
// frontend_pick_test.py runs this file from pytest.
import { test } from "node:test";
import assert from "node:assert/strict";
import { accessOf, countsText, coveredByServer, databaseTickUnderServer, likeContaining, noWholeServer, readable, selectionCounts,
  serverRowPick, serverTick } from "../../src/frontend/src/pages/pickState.ts";

test("a database filter becomes the SQL Server LIKE pattern for names containing it, its wildcards taken literally", () => {
  assert.equal(likeContaining(""), null);
  assert.equal(likeContaining("eng_0"), "%eng[_]0%");
  assert.equal(likeContaining("50%"), "%50[%]%");
  assert.equal(likeContaining("a[b]"), "%a[[]b]%");
  assert.equal(likeContaining("lfc.eng-1$#@"), "%lfc.eng-1$#@%");
});

test("a filter with a character the listing can't take reads the whole server instead", () => {
  assert.equal(likeContaining("eng 0"), null);
  assert.equal(likeContaining("eng'0"), null);
  assert.equal(likeContaining("x".repeat(127)), null, "longer than the 128 characters a pattern takes once wrapped");
});

test("a database's check reads open, closed or not known yet", () => {
  assert.equal(accessOf({ open: true }), "open");
  assert.equal(accessOf({ open: false }), "closed");
  assert.equal(accessOf({ open: null }), "unknown");
  assert.equal(accessOf(undefined), "unknown");
});

test("the whole server counts the databases its filter keeps, less those the login can't open", () => {
  const counts = selectionCounts({ matched: 201, closed: 101, unknown: 0, tables: 100_000 }, [], []);
  assert.deepEqual(counts, { databases: 100, tables: 100_000, counting: false, leftOut: 101 });
  assert.equal(countsText(counts, 201), "Chosen: 100 of 201 databases, 100,000 tables");
});

test("a whole server under a filter counts only what the filter keeps", () => {
  const counts = selectionCounts({ matched: 10, closed: 2, unknown: 0, tables: 8_000 }, [], []);
  assert.equal(countsText(counts, 201), "Chosen: 8 of 201 databases, 8,000 tables");
});

test("whole databases add their counted tables, and one not counted yet says the tables are counting", () => {
  assert.equal(countsText(selectionCounts(null, [1000, 1000], []), 201), "Chosen: 2 of 201 databases, 2,000 tables");
  const counting = selectionCounts(null, [1000, null], []);
  assert.equal(counting.counting, true);
  assert.equal(countsText(counting, 201), "Chosen: 2 of 201 databases");
});

test("single tables count one each, and a server not fully checked is still counting", () => {
  assert.equal(countsText(selectionCounts(null, [], [3, 1]), 201), "Chosen: 2 of 201 databases, 4 tables");
  assert.equal(countsText(selectionCounts(null, [], [1]), 1), "Chosen: 1 of 1 database, 1 table");
  const server = selectionCounts({ matched: 201, closed: 0, unknown: 150, tables: 51_000 }, [1000], [2]);
  assert.deepEqual(server, { databases: 203, tables: 52_002, counting: true, leftOut: 0 });
});

test("a database the login can't open is never read: only the open and the not yet checked ones are", () => {
  const seen = { a: { open: true }, b: { open: false }, c: undefined, d: { open: null } };
  assert.deepEqual(readable(["a", "b", "c", "d"], seen), ["a", "c", "d"]);
  assert.deepEqual(readable(["b"], seen), []);
});

test("the whole server less a database: unticking one keeps the rest, and the server row shows as chosen in part", () => {
  const whole = serverTick(noWholeServer, "", "none");
  assert.deepEqual(whole, { on: true, filter: "", excluded: [] });
  assert.equal(serverRowPick(whole, "", false), "all");
  const less = databaseTickUnderServer(whole, "lfc_test_002");
  assert.deepEqual(less, { on: true, filter: "", excluded: ["lfc_test_002"] });
  assert.equal(coveredByServer(less, "lfc_test_002"), false);
  assert.equal(coveredByServer(less, "LFC_ENG_001"), true, "every other database is still the server's");
  assert.equal(serverRowPick(less, "", false), "part");
  const more = databaseTickUnderServer(less, "lfc_test_003");
  assert.deepEqual(more.excluded, ["lfc_test_002", "lfc_test_003"]);
});

test("ticking a database unticked under the server takes it back, in any case of its name", () => {
  const less = { on: true, filter: "", excluded: ["lfc_test_002"] };
  assert.deepEqual(databaseTickUnderServer(less, "LFC_TEST_002"), { on: true, filter: "", excluded: [] });
});

test("a database the whole server isn't over takes its own tick", () => {
  assert.equal(databaseTickUnderServer(noWholeServer, "lfc_eng_001"), null);
  assert.equal(databaseTickUnderServer({ on: true, filter: "eng", excluded: [] }, "lfc_test_001"), null);
});

test("the server row in part from databases unticked under it gets them back, and ticked again it is unticked", () => {
  const less = { on: true, filter: "", excluded: ["lfc_test_002", "lfc_eng_009"] };
  const shownTestOnly = serverTick(less, "test", serverRowPick(less, "test", false));
  assert.deepEqual(shownTestOnly, { on: true, filter: "", excluded: ["lfc_eng_009"] }, "the whole server stays, filter and all");
  const all = serverTick(less, "", serverRowPick(less, "", false));
  assert.deepEqual(all, { on: true, filter: "", excluded: [] });
  assert.equal(serverRowPick(all, "", false), "all");
  assert.deepEqual(serverTick(all, "", "all"), { on: false, filter: "", excluded: [] });
});

test("a database unticked out of the page's sight leaves the row ticked for the databases the page shows", () => {
  const less = { on: true, filter: "", excluded: ["lfc_test_002"] };
  assert.equal(serverRowPick(less, "eng", false), "all");
});

test("the whole server's count leaves out each database unticked under it, and its tables once they are counted", () => {
  const counts = selectionCounts({ matched: 201, closed: 0, unknown: 0, tables: 100_500, excluded: [500] }, [], []);
  assert.equal(countsText(counts, 201), "Chosen: 200 of 201 databases, 100,000 tables");
  const counting = selectionCounts({ matched: 201, closed: 0, unknown: 0, tables: 100_500, excluded: [null] }, [], []);
  assert.equal(countsText(counting, 201), "Chosen: 200 of 201 databases");
});
