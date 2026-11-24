"use strict";

const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = readFileSync(require.resolve("../web/studio/experiments.js"), "utf8");

test("new experiments prefer the newest installed BAR build over the development placeholder", () => {
    const games = [
        "Beyond All Reason $VERSION",
        "Beyond All Reason test-9999-abcdef0",
        "Beyond All Reason test-10002-0000000",
        "Beyond All Reason test-10001-fffffff",
    ];
    const context = vm.createContext({games});
    vm.runInContext(source, context);
    assert.equal(vm.runInContext("preferredExperimentGame(games)", context), games[2]);
    assert.equal(vm.runInContext("preferredExperimentGame(games.slice(0, 1))", context), games[0]);
    assert.equal(vm.runInContext("preferredExperimentGame([])", context), "");
});

test("experiment game drafts distinguish explicit selection from a generated default", () => {
    const games = ["Beyond All Reason $VERSION", "Beyond All Reason test-10-a", "Beyond All Reason test-20-b"];
    const context = vm.createContext({games});
    vm.runInContext(source, context);
    for (const [game, selected, expected] of [
        [games[0], undefined, games[2]], [games[0], true, games[0]],
        [games[1], false, games[2]], [games[1], undefined, games[1]],
        ["Removed BAR build", true, games[2]],
    ]) {
        context.saved = {game, game_selected: selected};
        const result = vm.runInContext("experimentGameDraft(games, saved)", context);
        assert.equal(result.game, expected);
        assert.equal(result.game_selected, expected === game);
    }
    context.saved = {game: games[1]};
    assert.equal(vm.runInContext("experimentGameDraft(games, saved, games[1]).game", context), games[2]);
    context.saved.game_selected = true;
    assert.equal(vm.runInContext("experimentGameDraft(games, saved, games[1]).game", context), games[1]);
});

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
