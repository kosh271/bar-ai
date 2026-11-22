"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

global.window = {};
require("../web/studio/config.js");
require("../web/studio/editors.js");
const editors = window.StudioEditors;

test("relative shares handle decimals, invalid weights and totals larger than a finite number", () => {
    assert.deepEqual(editors.weightShares([0.25, 0.75, -4, Infinity, NaN]), [25, 75, 0, 0, 0]);
    assert.deepEqual(editors.weightShares([0, -2, NaN]), [0, 0, 0]);
    assert.deepEqual(editors.weightShares([1e308, 1e308]), [50, 50]);
});

test("production row changes retain the exact spelling of existing weights", () => {
    const text = '{"factory":{"armfac":{"unit":["armone"],"land":{"tier1":[1.2500]}}}}';
    const added = window.StudioConfig.changeFactoryUnit(text, "armfac", "armtwo", null);
    assert.match(added, /\[1\.2500,\s*0\]/);
    assert.match(window.StudioConfig.changeFactoryUnit(added, "armfac", "", 1), /\[1\.2500\]/);
});

test("unit picker combines friendly name or ID queries with faction and role filters", () => {
    const entries = [
        {id: "armtest", name: "Rock Thrower", faction: "arm", role: "artillery"},
        {id: "cortest", name: "Rock Thrower", faction: "cor", role: "assault"},
        {id: "custom", name: "Constructor", faction: "other", role: ""},
    ];
    assert.deepEqual(editors.filterUnits(entries, " THROWER ", "arm", "artillery"), [entries[0]]);
    assert.deepEqual(editors.filterUnits(entries, "cortest", "", ""), [entries[1]]);
    assert.deepEqual(editors.filterUnits(entries, "", "other", ""), [entries[2]]);
});

test("profile comparison distinguishes semantic edits from JSONC formatting and lists file changes", () => {
    const oldFiles = [
        {path: "same.json", text: '{"a":0, "b":[1.0,2,],}'},
        {path: "factory.json", text: '{"factory":{"armlab":{"unit":["armpw"],"land":{"tier1":[1]}}}}'},
        {path: "remove.json", text: '{}'},
    ];
    const newFiles = [
        {path: "same.json", text: '// formatted\n{"b":[1,2], "a":0}'},
        {path: "factory.json", text: '{"factory":{"armlab":{"unit":["armpw"],"land":{"tier1":[2]}}}}'},
        {path: "add.as", text: 'new();'},
    ];
    const result = editors.compareFiles(oldFiles, newFiles);
    assert.deepEqual(result.map(file => [file.path, file.kind]), [
        ["add.as", "added"], ["factory.json", "modified"], ["remove.json", "removed"], ["same.json", "formatting"],
    ]);
    const [change] = result[1].changes;
    assert.deepEqual(change.path, ["factory", "armlab", "land", "tier1", 0]);
    assert.deepEqual(change.units, ["armlab", "armpw"]);
    assert.deepEqual([change.left, change.right], ["1", "2"]);
    assert.deepEqual(result[3].changes, []);
});

test("line comparison displays numbered script hunks and does not conflate an absent side with an empty line", () => {
    const before = ["old();", ...Array(20).fill("unchanged();"), "oldTail();"].join("\n");
    const after = ["new();", ...Array(20).fill("unchanged();"), "newTail();"].join("\r\n");
    const lines = editors.lineDiff(before, after);
    assert.deepEqual(lines.slice(0, 2), [
        {left: 1, right: null, text: "old();", kind: "removed"},
        {left: null, right: 1, text: "new();", kind: "added"},
    ]);
    assert.equal(lines.filter(line => line.kind === "hunk").length, 1);
    assert.deepEqual(editors.lineDiff(null, "new();"), [{left: null, right: 1, text: "new();", kind: "added"}]);
});
