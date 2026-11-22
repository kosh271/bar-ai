"use strict";

const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const source = readFileSync(require.resolve("../web/studio/studio.js"), "utf8");

test("a new scan is polled while the previous match history is loading", async () => {
    const context = vm.createContext({window: {}, AbortController, setTimeout, clearTimeout});
    vm.runInContext(source.slice(0, source.indexOf('$("dialog-close").addEventListener')), context);
    await vm.runInContext(`(async () => {
        let releaseHistory, historyStarted;
        const entered = new Promise(resolve => { historyStarted = resolve; });
        loadMatchHistory = () => new Promise(resolve => { releaseHistory = resolve; historyStarted(); });
        updateMatchStatus = () => {};
        render = () => {};
        const settings = {bar_root: "BAR", engine: "engine"};
        state.settings = settings;
        const match = {id: "old", operation: "match", state: "completed"};
        const scan = {id: "new", operation: "scan", state: "completed", result: {content: {games: ["BAR"]}}};
        let backendStatus = match;
        api = async () => backendStatus;
        state.match.snapshot = match;
        const previous = pollMatch();
        await entered;
        state.match.scanSettingsKey = matchSettingsKey(settings);
        state.match.snapshot = {id: "new", operation: "scan", state: "running"};
        backendStatus = scan;
        await pollMatch(true);
        releaseHistory();
        await previous;
    })()`, context);
    assert.equal(vm.runInContext("state.match.content?.games[0]", context), "BAR");
});
