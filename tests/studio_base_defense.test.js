"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

global.window = {};
require("../web/studio/config.js");
const config = window.StudioConfig;

const basePath = ["porcupine", "base"];
const schedule = text => config.at(config.parse(text), basePath).children
    .map(({node}) => node.children.map(({node: field}) => field.value));

test("creates a base schedule in a faction layer with no local schedule", () => {
    const text = '{"porcupine":{"unit":{"legion":["legdeflector"]}, /* keep */ "land":[0],}}';
    const updated = config.editBaseDefense(text, null, [0, 1800]);
    assert.deepEqual(schedule(updated), [[0, 1800]]);
    assert.match(updated, /\/\* keep \*\//);
    assert.match(updated, /"land":\[0\]/);
});

test("adds, edits and removes scheduled builds while retaining comments", () => {
    const text = '{"porcupine":{"unit":{"armada":["armllt","armgate"]},"base":[[0, /* original time */ 420], // first\n [0,840],]}}';
    const added = config.editBaseDefense(text, null, [1, 1800]);
    assert.deepEqual(schedule(added), [[0, 420], [0, 840], [1, 1800]]);
    const edited = config.editBaseDefense(added, 2, [1, 2100]);
    assert.deepEqual(schedule(edited), [[0, 420], [0, 840], [1, 2100]]);
    const removed = config.editBaseDefense(edited, 0, null);
    assert.deepEqual(schedule(removed), [[0, 840], [1, 2100]]);
    assert.match(removed, /\/\/ first/);
    assert.match(removed, /\/\* original time \*\//);
});

test("rejects invalid time and slots absent from the selected layer", () => {
    const text = '{"porcupine":{"unit":{"armada":["armgate"]}}}';
    assert.throws(() => config.editBaseDefense(text, null, [0, -1]), /game time/);
    assert.throws(() => config.editBaseDefense(text, null, [1, 1800]), /unit slot/);
});
