"use strict";

const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const context = vm.createContext({
    window: {},
    document: {querySelectorAll: () => [], createElement: () => ({setAttribute() {}}),
        addEventListener() {}, defaultView: {addEventListener() {}}},
    MutationObserver: class {},
});
vm.runInContext(readFileSync(require.resolve("../web/studio/tooltips.js"), "utf8"), context);
const {compareDistance} = context.window.StudioTips;

test("distance comparisons span building widths, weapon ranges, blast diameters, and maps", () => {
    assert.match(compareDistance(1), /much smaller.*Grunt/i);
    assert.match(compareDistance(5), /less than half.*Tick/i);
    assert.match(compareDistance(10), /about half.*Tick/i);
    assert.match(compareDistance(15), /about one building-grid step/i);
    assert.match(compareDistance(20), /about.*width of a Grunt/i);
    assert.match(compareDistance(96), /about.*width of a Juggernaut/i);
    assert.match(compareDistance(1040), /smaller.*diameter of an Armada nuke blast/i);
    assert.match(compareDistance(4650), /Basilica.*long-range plasma cannon/i);
    assert.match(compareDistance(12288), /height of Supreme Isthmus/i);
});

test("distance comparisons handle zero, signed offsets, and missing values", () => {
    assert.equal(compareDistance(-96), compareDistance(96));
    assert.match(compareDistance(0), /no distance/i);
    assert.equal(compareDistance(NaN), "");
    assert.equal(compareDistance(null), "");
});
