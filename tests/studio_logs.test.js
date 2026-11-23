"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

global.window = {};
require("../web/studio/logs.js");
const L = window.StudioLogs;

const row = (text, category = "other") => ({text, category});
const build = rows => L.buildEntries(rows);

test("parses timestamp and frame, and leaves unstamped lines alone", () => {
    assert.deepEqual(L.parseLine("[t=00:08:40.030053][f=0157801] Error: crashed"),
        {stamped: true, time: "00:08:40.030", frame: 157801, body: "Error: crashed"});
    assert.deepEqual(L.parseLine("[t=00:00:01.5] [f=-000001] x"),
        {stamped: true, time: "00:00:01.500", frame: -1, body: "x"});
    assert.deepEqual(L.parseLine("[t=00:00:00.030900]   "), {stamped: true, time: "00:00:00.030", frame: null, body: "  "});
    assert.deepEqual(L.parseLine("    [A] Net"), {stamped: false, time: "", frame: null, body: "    [A] Net"});
});

test("severity splits AI script lines into errors, warnings and info", () => {
    assert.equal(L.severityOf("engine_error", "Error: sync failure"), "error");
    assert.equal(L.severityOf("engine_warning", "Warning: x"), "warn");
    assert.equal(L.severityOf("ai_script", "script.as (12, 5) : ERR Unexpected token"), "error");
    assert.equal(L.severityOf("ai_script", "Error: Skirmish AI BARb not found!"), "error");
    assert.equal(L.severityOf("ai_script", "Skirmish AI <x>: AngelScript runtime exception"), "error");
    assert.equal(L.severityOf("ai_script", "script.as (3, 1) : WARN unused"), "warn");
    assert.equal(L.severityOf("ai_script", "Skirmish AI <x>: Script: 'void AiUnitAdded()' not found!"), "none");
    assert.equal(L.severityOf("other", "Error: not classified by the server"), "none");
});

test("continuation lines inherit category and severity from the entry above", () => {
    const entries = build([
        row("[t=00:00:01.000000] Error: crashed", "engine_error"),
        row("  at frame 1"),
        row("  at frame 2"),
        row("[t=00:00:02.000000] fine"),
        row("  more detail"),
    ]);
    assert.deepEqual(entries.map(entry => entry.continuation), [false, true, true, false, true]);
    assert.deepEqual(entries.map(entry => entry.severity), ["error", "error", "error", "none", "none"]);
    assert.equal(entries[1].category, "engine_error");
    assert.equal(entries[4].category, "other");
    assert.deepEqual(L.countEntries(entries), {all: 5, error: 3, warn: 0, ai: 0, other: 2});
});

test("joinPages stitches a line that continues on the next page", () => {
    const rows = L.joinPages([
        {lines: [{text: "one", category: "other"}, {text: "two-a", category: "other"}], ends_with_newline: false, next_offset: 10},
        {lines: [{text: "two-b", category: "engine_error"}, {text: "three", category: "other"}], ends_with_newline: true, next_offset: 20},
        {lines: [{text: "four", category: "other"}], ends_with_newline: false, next_offset: null},
        {lines: [{text: "five", category: "other"}], ends_with_newline: true, next_offset: null},
    ]);
    assert.deepEqual(rows.map(item => item.text), ["one", "two-atwo-b", "three", "four", "five"]);
    assert.equal(rows[1].category, "engine_error");
});

test("search is literal and case-insensitive, and highlights every hit", () => {
    const pattern = L.searchPattern("  a.b ");
    assert.ok(pattern.test("xA.By"));
    assert.ok(!pattern.test("xAzBy"));
    assert.equal(L.searchPattern("   "), null);
    assert.deepEqual(L.splitHits("a.b and A.B!", pattern), [
        {text: "a.b", hit: true}, {text: " and ", hit: false}, {text: "A.B", hit: true}, {text: "!", hit: false}]);
    assert.deepEqual(L.splitHits("", pattern), [{text: "", hit: false}]);
    assert.deepEqual(L.splitHits("plain", null), [{text: "plain", hit: false}]);
});

const sample = () => build([
    row("[t=00:00:00.1] boot"),
    row("[t=00:00:00.2] Skirmish AI <x>: Script: 'f1' not found!", "ai_script"),
    row("[t=00:00:00.3] Skirmish AI <x>: Script: 'f22' not found!", "ai_script"),
    row("[t=00:00:00.4] Error: bad thing", "engine_error"),
    row("[t=00:00:00.5] between"),
    row("[t=00:00:00.6] still between"),
    row("[t=00:00:00.7] more"),
    row("[t=00:00:00.8] Error: bad thing", "engine_error"),
    row("[t=00:00:00.9] end"),
]);

test("filters, search and counts work across the whole log", () => {
    const entries = sample();
    assert.equal(L.computeView(entries, {}).shown, 9);
    assert.deepEqual(L.computeView(entries, {filter: "error"}).rows.map(item => item.entry.n), [4, 8]);
    assert.deepEqual(L.computeView(entries, {filter: "ai"}).rows.map(item => item.entry.n), [2, 3]);
    const searched = L.computeView(entries, {pattern: L.searchPattern("BETWEEN")});
    assert.deepEqual(searched.rows.map(item => item.entry.n), [5, 6]);
    assert.equal(searched.matched, 2);
    assert.deepEqual(L.computeView(entries, {filter: "error", pattern: L.searchPattern("bad")}).rows.length, 2);
    assert.equal(L.computeView(entries, {pattern: L.searchPattern("00:00:00.9")}).shown, 1);
    const framed = build([row("[t=00:00:00.900000][f=0175801] Error: boom", "engine_error")]);
    assert.equal(L.computeView(framed, {pattern: L.searchPattern("[f=0175801] Error")}).shown, 1);
});

test("context adds dimmed neighbours and gap markers only while filtering", () => {
    const entries = sample();
    const view = L.computeView(entries, {filter: "error", context: 1});
    assert.deepEqual(view.rows.map(item => item.gap ? `gap${item.hidden}` : item.entry.n + (item.context ? "c" : "")),
        ["gap2", "3c", "4", "5c", "gap1", "7c", "8", "9c"]);
    assert.deepEqual(L.computeView(entries, {context: 5}).rows.some(item => item.gap), false);
    assert.deepEqual(L.computeView(entries, {filter: "error", context: 0}).rows.some(item => item.gap), false);
});

test("collapse repeats keeps the first line and reports the count", () => {
    const entries = sample();
    const view = L.computeView(entries, {collapse: true});
    assert.deepEqual(view.rows.map(item => item.entry.n), [1, 2, 4, 5, 6, 7, 9]);
    assert.equal(view.rows.find(item => item.entry.n === 2).repeat, 2);
    assert.equal(view.rows.find(item => item.entry.n === 4).repeat, 2);
    assert.equal(view.rows.find(item => item.entry.n === 1).repeat, 0);
});

test("collapse also hides the continuation lines of a hidden repeat", () => {
    const entries = build([
        row("[t=00:00:00.1] Error: boom", "engine_error"), row("  trace a"),
        row("[t=00:00:00.2] Error: boom", "engine_error"), row("  trace b"),
    ]);
    assert.deepEqual(L.computeView(entries, {collapse: true}).rows.map(item => item.entry.n), [1, 2]);
});

test("row limit truncates and reports it", () => {
    const entries = build(Array.from({length: 5}, (_, i) => row(`[t=00:00:0${i}.0] line ${i}`)));
    const view = L.computeView(entries, {maxRows: 3});
    assert.equal(view.rows.length, 3);
    assert.equal(view.truncated, true);
});

test("log sections advance the source line cursor, including mid-line boundaries", async () => {
    const pages = new Map([
        [0, {lines: [row("one"), row("two-a")], ends_with_newline: false, next_offset: 32}],
        [32, {lines: [row("two-b"), row("three")], ends_with_newline: true, next_offset: null}],
    ]);
    const load = async offset => pages.get(offset);
    const first = await L.readSection(load, {offset: 0, firstLine: 1}, () => {}, 1);
    assert.deepEqual(first.next, {offset: 32, firstLine: 2, continued: true});
    const second = await L.readSection(load, first.next, () => {}, 1);
    assert.deepEqual(L.buildEntries(second.rows, false, first.next.firstLine).map(entry => [entry.n, entry.raw]),
        [[2, "two-b"], [3, "three"]]);
    assert.equal(second.next, null);
});

test("a line started at the end of a section moves into the next section intact", async () => {
    const pages = new Map([
        [0, {lines: [row("startup")], ends_with_newline: true, next_offset: 8}],
        [8, {lines: [row("Error: Access ")], ends_with_newline: false, next_offset: 40}],
        [40, {lines: [row("violation"), row("shutdown")], ends_with_newline: true, next_offset: null}],
    ]);
    const load = async offset => pages.get(offset);
    const first = await L.readSection(load, {offset: 0, firstLine: 1}, () => {}, 2);
    assert.deepEqual(first.rows.map(line => line.text), ["startup"]);
    const second = await L.readSection(load, first.next, () => {}, 2);
    assert.deepEqual(L.buildEntries(second.rows, false, first.next.firstLine).map(line => [line.n, line.raw]),
        [[2, "Error: Access violation"], [3, "shutdown"]]);
});

test("banner blocks fold by default but not while filtering, and a leading BOM is ignored", () => {
    const entries = build([
        row("﻿[t=00:00:00.1] start"),
        row("[t=00:00:00.2] ============== <Log Sections> =============="),
        row("[t=00:00:00.3]   one"), row("    two"),
        row("[t=00:00:00.4] ============== </Log Sections> =============="),
        row("[t=00:00:00.5] after one"),
        row("[t=00:00:00.6] ============== <Unclosed> =============="),
        row("[t=00:00:00.7] tail"),
    ]);
    assert.equal(entries[0].time, "00:00:00.100");
    assert.equal(entries[1].foldCount, 3);
    assert.equal(entries[6].foldCount, 0);
    const folded = L.computeView(entries, {fold: true});
    assert.deepEqual(folded.rows.map(item => item.entry.n), [1, 2, 6, 7, 8]);
    assert.deepEqual(folded.rows[1].fold, {index: 1, count: 3, open: false});
    const open = L.computeView(entries, {fold: true, expanded: new Set([1])});
    assert.equal(open.rows.length, 8);
    assert.equal(open.rows[1].fold.open, true);
    assert.equal(L.computeView(entries, {fold: false}).rows.length, 8);
    const searched = L.computeView(entries, {fold: true, pattern: L.searchPattern("one")});
    assert.deepEqual(searched.rows.map(item => item.entry.n), [3, 6]);
});
