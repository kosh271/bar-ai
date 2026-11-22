"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

global.window = {};
require("../web/studio/config.js");
const config = window.StudioConfig;

const land = text => config.at(config.parse(text), ["porcupine", "land"]).children
    .map(({node}) => node.value);

test("appends a second copy, changes a slot, and reorders the defense sequence", () => {
    const original = '{"porcupine":{"land":[0, 3, 12],"water":[1]}}';
    const added = config.editDefenseSequence(original, "land", null, 12);
    assert.deepEqual(land(added), [0, 3, 12, 12]);
    const moved = config.editDefenseSequence(added, "land", 2, "up");
    assert.deepEqual(land(moved), [0, 12, 3, 12]);
    const edited = config.editDefenseSequence(moved, "land", 3, 8);
    assert.deepEqual(land(edited), [0, 12, 3, 8]);
    const later = config.editDefenseSequence(edited, "land", 1, "down");
    assert.deepEqual(land(later), [0, 3, 12, 8]);
    assert.match(later, /"water":\[1\]/);
});

test("keeps sequence comments and trailing commas through structural edits", () => {
    const original = '{"porcupine":{"land":[\r\n  0, // opening defense\r\n  12, /* late-game defense */\r\n]}}';
    const appended = config.editDefenseSequence(original, "land", null, 12);
    const moved = config.editDefenseSequence(appended, "land", 0, "down");
    const removed = config.editDefenseSequence(moved, "land", 1, null);
    assert.deepEqual(land(removed), [12, 12]);
    assert.match(removed, /\/\/ opening defense\r\n/);
    assert.match(removed, /\/\* late-game defense \*\/\r\n/);
});

test("supports removing the last slot and adding to an explicitly empty order", () => {
    const original = '{"porcupine":{"land":[0 /* keep */]}}';
    const removed = config.editDefenseSequence(original, "land", 0, null);
    assert.deepEqual(land(removed), []);
    assert.match(removed, /\/\* keep \*\//);
    assert.deepEqual(land(config.editDefenseSequence(removed, "land", null, 12)), [12]);
});

test("rejects an absent terrain order, invalid row, or invalid slot", () => {
    const original = '{"porcupine":{"land":[0]}}';
    assert.throws(() => config.editDefenseSequence(original, "water", null, 12), /unavailable/);
    assert.throws(() => config.editDefenseSequence(original, "base", null, 12), /terrain/);
    assert.throws(() => config.editDefenseSequence(original, "land", 0, "up"), /move/);
    assert.throws(() => config.editDefenseSequence(original, "land", 1, null), /row/);
    assert.throws(() => config.editDefenseSequence(original, "land", null, -1), /slot/);
});
