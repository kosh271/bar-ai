"use strict";

const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = readFileSync(require.resolve("../web/studio/experiments.js"), "utf8");
const headingLabel = path => [...path].map(heading => heading.label).filter(Boolean).join(" · ");

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

test("matchup matrices count row results, resolved factions, and same-faction games", () => {
    const experiment = {
        mode: "tournament",
        rankings: [
            {id: "v1", label: "Fixture AI · hard · Random"},
            {id: "v2", label: "Other AI · easy · Rotate"},
            {id: "v3", label: "Fixture AI · hard · Cortex"},
            {id: "v4", label: "Unused AI · hard · Legion"},
        ],
        schedule: [
            {index: 1, side1: "v1", side2: "v2", players: [
                {ally: 0, short_name: "fixture", version: "1", profile: "hard", faction: "Armada"},
                {ally: 1, short_name: "other", version: "1", profile: "easy", faction: "Cortex"},
            ]},
            {index: 4, side1: "v3", side2: "v2", players: [
                {ally: 0, short_name: "fixture", version: "1", profile: "hard", faction: "Cortex"},
                {ally: 1, short_name: "other", version: "1", profile: "easy", faction: "Cortex"},
            ]},
        ],
        games: [
            {index: 1, side1: "v1", side2: "v2", outcome: "side1"},
            {index: 2, side1: "v2", side2: "v1", factions: ["Cortex", "Armada"], outcome: "side2"},
            {index: 3, side1: "v1", side2: "v2", factions: ["Armada", "Cortex"], outcome: "draw"},
            {index: 4, side1: "v3", side2: "v2", outcome: "side2"},
            {index: 5, side1: "v3", side2: "v1", factions: ["Cortex", "Armada"], outcome: "side1"},
            {index: 6, side1: "v2", side2: "v4", factions: ["Cortex", "Legion"], outcome: "timeout"},
            {index: 7, side1: "v2", side2: "v3", factions: ["Cortex", "Cortex"], outcome: "failed"},
            {index: 8, side1: "v4", side2: "v1", factions: ["Legion", "Armada"], outcome: "cancelled"},
        ],
    };
    const context = vm.createContext({experiment});
    vm.runInContext(source, context);
    const [ai, faction, pairing] = vm.runInContext("experimentComparisons(experiment)", context);
    function record(matrix, rowLabel, columnLabel) {
        const key = label => [...matrix.headings].find(([, path]) => headingLabel(path) === label)?.[0];
        const entry = matrix.records.get(JSON.stringify([key(rowLabel), key(columnLabel)]));
        return entry && [entry.wins, entry.losses, entry.draws, entry.games];
    }
    assert.deepEqual([...ai.headings.values()].map(headingLabel), ["Fixture AI · hard", "Other AI · easy", "Unused AI · hard"]);
    assert.deepEqual(record(ai, "Fixture AI · hard", "Other AI · easy"), [2, 1, 1, 4]);
    assert.deepEqual(record(ai, "Other AI · easy", "Fixture AI · hard"), [1, 2, 1, 4]);
    assert.equal(record(ai, "Fixture AI · hard", "Fixture AI · hard")[3], 1);
    assert.deepEqual(record(faction, "Armada", "Cortex"), [2, 1, 1, 4]);
    assert.equal(record(faction, "Cortex", "Cortex")[3], 1);
    assert.equal(record(faction, "Cortex", "Legion"), undefined);
    assert.deepEqual(record(pairing, "Fixture AI · hard · Armada", "Other AI · easy · Cortex"), [2, 0, 1, 3]);
    assert.deepEqual(record(pairing, "Other AI · easy · Cortex", "Fixture AI · hard · Cortex"), [1, 0, 0, 1]);
});

test("older results use fixed faction labels and leave unknown factions out of faction comparisons", () => {
    const experiment = {
        mode: "tournament",
        rankings: [
            {id: "v1", label: "Fixture AI · hard · Armada"},
            {id: "v2", label: "Other AI · easy · Cortex"},
            {id: "v3", label: "Third AI · hard · Random"},
        ],
        games: [
            {side1: "v1", side2: "v2", outcome: "side2"},
            {side1: "v1", side2: "v3", outcome: "side1"},
        ],
    };
    const context = vm.createContext({experiment});
    vm.runInContext(source, context);
    const [ai, faction, pairing] = vm.runInContext("experimentComparisons(experiment)", context);
    assert.equal(ai.records.size, 4);
    assert.deepEqual([...faction.headings.values()].map(headingLabel), ["Armada", "Cortex"]);
    assert.equal(faction.records.get('["Cortex","Armada"]').wins, 1);
    assert.deepEqual([...pairing.headings.values()].map(headingLabel), ["Fixture AI · hard · Armada", "Other AI · easy · Cortex"]);
});
