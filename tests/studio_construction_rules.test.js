"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

global.window = {};
require("../web/studio/scripts.js");
const scripts = window.StudioScripts;

const pathname = "script/main.as";
const main = 'namespace Main { void AiMain() {} }\r\n';
const ratio = {trigger: "armafus", target: "armgate", count: 5, radius: 0};
const cluster = {trigger: "armanni", target: "armgate", count: 2, radius: 400};
const files = text => [{path: pathname, text}];

test("construction rules round-trip without changing the profile's original script", () => {
    const edited = scripts.writeConstructionRules(files(main), [ratio, cluster]);
    assert.deepEqual(scripts.readConstructionRules(files(edited)).rules, [ratio, cluster]);
    assert.match(edited, /^namespace Main \{ void AiMain\(\) \{\} \}\r\n/);
    assert.match(edited, /void AiUnitFinished\(CCircuitUnit@ unit\)/);
    assert.match(edited, /void AiUnitDestroyed\(CCircuitUnit@ unit\)/);
    assert.doesNotMatch(edited, /[^\r]\n/);
    assert.equal(scripts.writeConstructionRules(files(edited), []), main);
});

test("construction rules protect user callbacks and edited generated code", () => {
    const custom = [{path: pathname, text: '#include "hook.as"\n' + main},
        {path: "script/hook.as", text: "namespace Main { void AiUnitFinished(CCircuitUnit@ u) {} }"}];
    assert.throws(() => scripts.writeConstructionRules(custom, [ratio]), /already defines/);
    const edited = scripts.writeConstructionRules(files(main), [ratio]).replace("Task::Priority::NORMAL", "Task::Priority::HIGH");
    assert.throws(() => scripts.writeConstructionRules(files(edited), []), /was edited/);
});

test("stock-style cyclic includes are traversed once when checking callbacks", () => {
    const cyclic = [{path: pathname, text: '#include "factory.as"\n' + main},
        {path: "script/factory.as", text: '#include "commander.as"\n'},
        {path: "script/commander.as", text: '#include "factory.as"\n'}];
    assert.match(scripts.writeConstructionRules(cyclic, [ratio]), /namespace StudioConstruction/);
});

test("construction rules reject invalid values, direct recursion and duplicates", () => {
    assert.throws(() => scripts.writeConstructionRules(files(main), [{...ratio, count: 0}]), /count/);
    assert.throws(() => scripts.writeConstructionRules(files(main), [{...ratio, target: ratio.trigger}]), /different/);
    assert.throws(() => scripts.writeConstructionRules(files(main), [ratio, ratio]), /duplicate/i);
});
