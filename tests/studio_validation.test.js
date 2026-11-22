"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
global.window = {};
require("../web/studio/config.js");
require("../web/studio/scripts.js");
require("../web/studio/openings.js");
const {StudioConfig: C, StudioScripts: S} = window;
const file = (path, text) => ({path, text});
const config = (name, value) => file("config/" + name + ".json", JSON.stringify(value));

test("stock init profile order follows a forwarding include and applies option combinations in source order", () => {
    const files = [
        file("script/init.as", '#include "startup/init.as" // stock entry'),
        file("script/startup/init.as", `
            #include "helper.as"
            namespace Init {
            SInitInfo AiInit() {
                SInitInfo data;
                @data.profile = @(array<string> = {"behaviour", "factory"});
                AddLayers(data);
                if (string(aiSetupMgr.GetModOptions()["experimentalextraunits"]) == "1") {
                    data.profile.insertAt(data.profile.length(), {"behaviour_extra_units"});
                }
                return data;
            }}
        `),
        file("script/startup/helper.as", `
            void AddLayers(SInitInfo &inout data) {
                if (string(aiSetupMgr.GetModOptions()["experimentallegionfaction"]) == "1") {
                    data.profile.insertAt(data.profile.length(), {"behaviour_leg", "factory_leg"});
                }
                if (string(aiSetupMgr.GetModOptions()["scavunitsforplayers"]) == "1") {
                    data.profile.insertAt(data.profile.length(), {"behaviour_scav_units"});
                }
                if (string(aiSetupMgr.GetModOptions()["scav_swarmmode"]) == "1")
                    data.profile.insertAt(data.profile.length(), {"behaviour_swarm"});
            }
        `),
        ...["behaviour", "factory", "behaviour_leg", "factory_leg", "behaviour_scav_units", "behaviour_extra_units",
            "behaviour_swarm"].map(name => config(name, {}))
    ];
    const result = S.layerPresets(files);
    assert.deepEqual(result.warnings, []);
    assert.deepEqual(result.presets.find(preset => preset.name.startsWith("Standard")).order,
        ["config/behaviour.json", "config/factory.json"]);
    assert.deepEqual(result.presets.find(preset => preset.name.includes("Legion") && preset.name.includes("Extra units") &&
        preset.name.includes("Scav") && preset.name.includes("Swarm")).order,
        ["config/behaviour.json", "config/factory.json", "config/behaviour_leg.json", "config/factory_leg.json",
            "config/behaviour_scav_units.json", "config/behaviour_swarm.json", "config/behaviour_extra_units.json"]);
});

test("custom layer branches are candidates and computed filenames do not become static layers", () => {
    const files = [
        file("script/init.as", `SInitInfo AiInit() {
            @data.profile = @(array<string> = {"factory"});
            if (aiMap.GetName() == "Example") { LoadConfig("config/factory_map.json"); }
            if (dynamic) { data.profile.insertAt(data.profile.length(), {runtimeName}); }
            for (uint i = 0; i < 3; i++) { LoadConfig("config/factory_loop.json"); }
            // LoadConfig("config/factory_comment.json");
            AiLog("LoadConfig(\\\"config/factory_string.json\\\")");
        }`),
        ...["factory", "factory_map", "factory_loop", "factory_comment", "factory_string"].map(name => config(name, {}))
    ];
    const result = S.layerPresets(files);
    assert.deepEqual(result.presets.find(preset => preset.name.startsWith("Standard")).order, ["config/factory.json"]);
    assert.ok(result.presets.some(preset => preset.name.includes("custom") &&
        preset.order.at(-1) === "config/factory_map.json"));
    assert.ok(result.warnings.some(issue => /dynamic|computed/i.test(issue.message)));
    assert.ok(result.presets.every(preset => !preset.order.some(path => /loop|comment|string/.test(path))));
});

test("branching helper expansion is bounded and reports incomplete layer analysis", () => {
    const helpers = Array.from({length: 20}, (_, i) =>
        `void Helper${i}() { Helper${i + 1}(); Helper${i + 1}(); }`).join("\n");
    const result = S.layerPresets([
        file("script/init.as", `void AiInit() { Helper0(); }\n${helpers}
            void Helper20() { LoadConfig("factory"); }`),
        config("factory", {})
    ]);
    assert.ok(result.warnings.some(issue => /helper expansion/.test(issue.message)));
});
