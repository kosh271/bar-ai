"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

global.window = {};
require("../web/studio/config.js");
const config = window.StudioConfig;

test("creates an independent defense chain without changing other actions or comments", () => {
    const text = '{"build_chain":{"energy":{"armafus":{"hub":[[{"unit":"armnanotc"}, /* keep */],]}}}}';
    const updated = config.addDefenseAction(text, "energy", "armafus", null, "armgate", "back", 100, 20);
    const hub = config.at(config.parse(updated), ["build_chain", "energy", "armafus", "hub"]);
    assert.equal(hub.children.length, 2);
    assert.equal(config.at(hub, [0, 0, "unit"]).value, "armnanotc");
    assert.equal(config.at(hub, [1, 0, "unit"]).value, "armgate");
    assert.equal(config.at(hub, [1, 0, "category"]).value, "defence");
    assert.equal(config.at(hub, [1, 0, "offset", "back"]).value, 100);
    assert.equal(config.at(hub, [1, 0, "condition", "chance"]).value, 0.2);
    assert.match(updated, /\/\* keep \*\//);
});

test("appends an unconditional action to an existing chain", () => {
    const text = '{"build_chain":{"defence":{"armanni":{"hub":[[{"unit":"armamd"}]]}}}}';
    const updated = config.addDefenseAction(text, "defence", "armanni", 0, "armgate", "left", 160, 100);
    const hub = config.at(config.parse(updated), ["build_chain", "defence", "armanni", "hub"]);
    assert.equal(hub.children.length, 1);
    assert.equal(config.at(hub, [0, 1, "offset", "left"]).value, 160);
    assert.equal(config.at(hub, [0, 1, "condition"]), null);
});

test("creates a missing completion trigger and keeps other trigger settings", () => {
    const inputs = [
        '{"porcupine":{}}',
        '{"build_chain":{"big_gun":{/* keep */}}}',
        '{"build_chain":{"big_gun":{"armbrtha":{"porc":true /* keep */}}}}',
    ];
    for (const text of inputs) {
        const updated = config.addDefenseAction(text, "big_gun", "armbrtha", null, "armgate", "front", 80);
        const trigger = config.at(config.parse(updated), ["build_chain", "big_gun", "armbrtha"]);
        assert.equal(config.at(trigger, ["hub", 0, 0, "unit"]).value, "armgate");
        if (text.includes('"porc"')) assert.equal(config.at(trigger, ["porc"]).value, true);
        if (text.includes("/* keep */")) assert.match(updated, /\/\* keep \*\//);
    }
});

test("rejects invalid probability, direction and unsupported existing structures", () => {
    const add = (text, chain = null, direction = "front", chance) =>
        config.addDefenseAction(text, "energy", "armafus", chain, "armgate", direction, 80, chance);
    assert.throws(() => add("{}", null, "front", 101), /chance/i);
    assert.throws(() => add("{}", null, "front", NaN), /chance/i);
    assert.throws(() => add("{}", null, "forward"), /direction/i);
    assert.throws(() => add("{}", 1), /chain/i);
    assert.throws(() => add('{"build_chain":{"energy":{"armafus":{"hub":true}}}}'), /chain/i);
});
