"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

global.window = {};
require("../web/studio/config.js");
require("../web/studio/editors.js");
const editors = window.StudioEditors;

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
