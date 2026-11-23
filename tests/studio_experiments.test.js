"use strict";

const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = readFileSync(require.resolve("../web/studio/experiments.js"), "utf8");

test("tournament map pool keeps the highest installed version and resolves the preferred map", () => {
    const names = [
        "All That Glitters v2.9", "All That Glitters Extended v1.0", "all_that_glitters_V2.10",
        "All That Glitters v2.9.2", "All That Glitters Extended v1.0.2",
        "Claymore 3", "Claymore 3.0.4", "Salt Reef v0.9", "Salt Reef 1.1",
        "Hotstepper 5 1.2.1", "Hotstepper 6 1.2.1", "Unversioned Map",
        "Crimson Bay v1.1a", "Crimson Bay v1.1b",
    ];
    const context = vm.createContext({maps: names.map(name => ({name}))});
    vm.runInContext(source, context);
    const {available, selected} = vm.runInContext(
        'tournamentMapPool(maps, "All That Glitters v2.8")', context
    );
    assert.deepEqual(Array.from(selected), ["all_that_glitters_V2.10"]);
    assert.deepEqual(Array.from(available, map => map.name).sort(), [
        "all_that_glitters_V2.10", "All That Glitters Extended v1.0.2", "Claymore 3.0.4",
        "Salt Reef 1.1", "Hotstepper 5 1.2.1", "Hotstepper 6 1.2.1", "Unversioned Map",
        "Crimson Bay v1.1b",
    ].sort());
    const saved = vm.runInContext(
        'tournamentMapPool(maps, "", ["Salt Reef v0.9", "Removed Map", "Claymore 3"]).selected', context
    );
    assert.deepEqual(Array.from(saved), ["Salt Reef 1.1", "Claymore 3.0.4"]);
});
