"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

global.window = {};
require("../web/studio/config.js");
require("../web/studio/scripts.js");
require("../web/studio/openings.js");

const scripts = window.StudioScripts;
const config = window.StudioConfig;
const localProfiles = process.env.STUDIO_TEST_PROFILE_ROOT || path.join(__dirname, "..", "projects", "Willtest", "profiles");

function readProfileFiles(profileDir) {
    const out = [];
    function walk(dir) {
        for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) walk(full);
            else if (/\.(?:as|json)$/i.test(entry.name)) {
                out.push({
                    path: path.relative(profileDir, full).replace(/\\/g, "/"),
                    text: fs.readFileSync(full, "utf8")
                });
            }
        }
    }
    walk(profileDir);
    return out;
}

test("inspectScript parses namespace constants, @studio metadata, time units, and BARb hooks", () => {
    const source = [
        '#include "../__shared__/define.as"',
        'namespace Sample {',
        '// @studio label="Air threshold" min=10 max=1000 page="combat" description="Enemy air metal"',
        'const float airLimit = 150.f;',
        'const int interval = 15 * SECOND;',
        'const int delay = 2 * MINUTE;',
        'const int negOffset = -5;',
        'const bool active = true;',
        'const string tag = "alpha";',
        '// const int commented = 99;',
        'const string ignored = "const int fake = 1;";',
        'string nonConst = "const int fake2 = 2;";',
        'class Helper {',
        '    const int insideClass = 7;',
        '    void AiMakeTask(CCircuitUnit@ u) {}',
        '}',
        'IUnitTask@ AiMakeTask(CCircuitUnit@ unit) {',
        '    const int insideFn = 42;',
        '    return null;',
        '}',
        '}',
        'void AiMain() {}'
    ].join("\n");

    const info = scripts.inspectScript(source);
    assert.deepEqual(info.includes.map(i => i.name), ["../__shared__/define.as"]);
    assert.deepEqual(info.hooks.map(h => [h.namespace, h.name, h.line]), [
        ["Sample", "AiMakeTask", 17],
        ["", "AiMain", 22]
    ]);
    assert.deepEqual(
        info.constants.map(c => ({
            namespace: c.namespace, name: c.name, type: c.type,
            value: c.value, unit: c.unit, line: c.line, meta: c.meta
        })),
        [
            {
                namespace: "Sample", name: "airLimit", type: "float", value: 150, unit: null, line: 4,
                meta: {label: "Air threshold", min: 10, max: 1000, page: "combat", description: "Enemy air metal"}
            },
            {namespace: "Sample", name: "interval", type: "int", value: 15, unit: "SECOND", line: 5, meta: null},
            {namespace: "Sample", name: "delay", type: "int", value: 2, unit: "MINUTE", line: 6, meta: null},
            {namespace: "Sample", name: "negOffset", type: "int", value: -5, unit: null, line: 7, meta: null},
            {namespace: "Sample", name: "active", type: "bool", value: true, unit: null, line: 8, meta: null},
            {namespace: "Sample", name: "tag", type: "string", value: "alpha", unit: null, line: 9, meta: null},
            {namespace: "Sample", name: "ignored", type: "string", value: "const int fake = 1;", unit: null, line: 11, meta: null}
        ]
    );
    const air = info.constants[0];
    assert.equal(air.column, source.split("\n")[3].indexOf("150.f") + 1);
    assert.equal(info.constants[3].column, source.split("\n")[6].indexOf("-5") + 1);
    assert.equal(info.hooks.length, 2);
    assert.deepEqual(info.hooks.map(h => [h.namespace, h.name, h.line]), [
        ["Sample", "AiMakeTask", 17],
        ["", "AiMain", 22]
    ]);

    let patched = scripts.patchScriptLiteral(source, air, 200);
    assert.match(patched, /const float airLimit = 200\.0f;/);
    patched = scripts.patchScriptLiteral(patched, scripts.inspectScript(patched).constants[1], 30);
    assert.match(patched, /const int interval = 30 \* SECOND;/);
    patched = scripts.patchScriptLiteral(patched, scripts.inspectScript(patched).constants[4], false);
    assert.match(patched, /const bool active = false;/);
    patched = scripts.patchScriptLiteral(patched, scripts.inspectScript(patched).constants[5], "beta");
    assert.match(patched, /const string tag = "beta";/);

    assert.throws(() => scripts.patchScriptLiteral(source, air, 5), /at least 10/);
    assert.throws(() => scripts.patchScriptLiteral(source, air, 2000), /at most 1000/);
    assert.throws(() => scripts.patchScriptLiteral(source, info.constants[1], 1.5), /whole integer/);
    assert.throws(() => scripts.patchScriptLiteral(source, info.constants[4], "yes"), /true or false/);
    assert.throws(() => scripts.patchScriptLiteral(source, info.constants[5], 'bad"quote'), /single-line string/);
});

test("discoverScriptParameters collects numeric constants across files and skips syntax errors", () => {
    const params = scripts.discoverScriptParameters([
        {path: "script/a.as", text: "const uint cap = 10u;\nconst bool flag = true;"},
        {path: "script/broken.as", text: "const int x = (1;"},
        {path: "config/a.json", text: "{}"}
    ]);
    assert.equal(params.length, 1);
    assert.equal(params[0].path, "script/a.as");
    assert.equal(params[0].name, "cap");
    assert.equal(params[0].value, 10);
});

test("readInitLayers and writeInitLayers edit conditional startup layers while preserving logs and comments", () => {
    const files = [
        {path: "script/init.as", text: `namespace Init {
SInitInfo AiInit() {
    //dictionary@ mo = aiSetupMgr.GetModOptions();
    SInitInfo data;
    @data.profile = @(array<string> = {"behaviour", "block_map", "build_chain", "commander", "economy", "factory", "response"});
    if (string(aiSetupMgr.GetModOptions()["experimentallegionfaction"]) == "1") {
        AiLog("Inserting Legion");
        Side::LEGION = aiSideMasker.GetTypeMask("legion");
        data.profile.insertAt(data.profile.length(), {"behaviour_leg"});
    }
    if (string(aiSetupMgr.GetModOptions()["scavunitsforplayers"]) == "1") {
        data.profile.insertAt(data.profile.length(), {"behaviour_scav_units"});
    }
    if (string(aiSetupMgr.GetModOptions()["experimentalextraunits"]) == "1") {
        data.profile.insertAt(data.profile.length(), {"behaviour_extra_units"});
    }
    return data;
}
}`},
        ...["behaviour", "block_map", "build_chain", "commander", "economy", "factory", "response",
            "behaviour_leg", "behaviour_scav_units", "behaviour_extra_units"].map(layer =>
            ({path: `config/${layer}.json`, text: "{}"}))
    ];
    const model = scripts.readInitLayers(files);

    assert.deepEqual(model.base, [
        "behaviour", "block_map", "build_chain", "commander", "economy", "factory", "response"
    ]);
    assert.deepEqual(model.branches.map(b => b.option), [
        "experimentallegionfaction", "scavunitsforplayers", "experimentalextraunits"
    ]);

    const updatedText = scripts.writeInitLayers(files, {
        base: ["response", "behaviour", "block_map", "build_chain", "commander", "economy", "factory"],
        branches: model.branches.map(b => b.option === "experimentallegionfaction"
            ? {...b, layers: [...b.layers, "response_leg"]}
            : b)
    });
    assert.match(updatedText, /Side::LEGION = aiSideMasker\.GetTypeMask\("legion"\);/);
    assert.match(updatedText, /\/\/dictionary@ mo = aiSetupMgr\.GetModOptions\(\);/);
    assert.match(updatedText, /AiLog\("Inserting Legion"\);/);

    const updatedFiles = [
        ...files.map(f => f.path === model.file.path ? {...f, text: updatedText} : f),
        {path: "config/response_leg.json", text: "{}"}
    ];
    const reloaded = scripts.readInitLayers(updatedFiles);
    assert.equal(reloaded.base[0], "response");
    assert.equal(reloaded.branches[0].layers.at(-1), "response_leg");

    const presets = scripts.layerPresets(updatedFiles);
    assert.equal(presets.warnings.length, 0);
    assert.equal(presets.presets[0].order[0], "config/response.json");

    assert.throws(() => scripts.writeInitLayers(files, {base: [], branches: []}), /At least one/);
    assert.throws(() => scripts.writeInitLayers(files, {base: ["a", "a"], branches: []}), /Duplicate/);
    assert.throws(() => scripts.writeInitLayers(files, {base: ["../bad"], branches: []}), /Unsupported/);
});

test("readInitLayers follows a forwarding include", () => {
    const files = [
        {path: "script/init.as", text: '#include "startup/real_init.as"\n'},
        {path: "script/startup/real_init.as", text: 'SInitInfo AiInit() { SInitInfo data; @data.profile = @(array<string> = {"factory"}); return data; }'}
    ];
    const model = scripts.readInitLayers(files);
    assert.equal(model.file.path, "script/startup/real_init.as");
    assert.deepEqual(model.base, ["factory"]);
    const next = scripts.writeInitLayers(files, {base: ["config/economy.json", "factory"], branches: []});
    assert.match(next, /\{"economy", "factory"\}/);
});

test("recruit rule edits preserve factory switching hooks and round-trip exactly", () => {
    const factoryFile = {path: "script/manager/factory.as", text: `namespace Factory {
IUnitTask@ AiMakeTask(CCircuitUnit@ unit)
{
    return aiFactoryMgr.DefaultMakeTask(unit);
}
void AiTaskRemoved(IUnitTask@ task, bool done)
{
}
bool ShouldSwitch() {
    int switchDelay = AiRandom(90, 120) * SECOND;
    float change = pow((ai.frame - lastSwitchFrame), 0.9);
    return change > (aiEconomyMgr.metal.current * 7) && ai.frame > switchDelay;
}
}
`};
    const files = [factoryFile];

    const fighter = {
        factory: "armap", unit: "armfig", target: 30, startMinutes: 2,
        cooldownSeconds: 0, yieldNormal: false, requireMetal: false,
        milestones: [], enemyRole: "", enemyCost: 0
    };
    const bomber = {
        factory: "armaap", unit: "armpnix", target: 50, startMinutes: 0,
        cooldownSeconds: 15, yieldNormal: true, requireMetal: true,
        milestones: ["armsilo", "armamd"], enemyRole: "air", enemyCost: 150
    };

    const editedText = scripts.writeRecruitRules(files, [fighter, bomber]);
    const editedFiles = files.map(f => f.path === factoryFile.path ? {...f, text: editedText} : f);

    const roundTripped = scripts.readRecruitRules(editedFiles);
    assert.deepEqual(roundTripped.rules, [fighter, bomber]);
    assert.equal(roundTripped.original, factoryFile.text);

    // Ensure existing factory switching regexes in studio.js still match uniquely.
    const cleanEdited = scripts.mask(editedText);
    assert.equal([...cleanEdited.matchAll(/AiRandom\s*\(\s*\d+\s*,\s*\d+\s*\)\s*\*\s*SECOND/g)].length, 1);
    assert.equal([...cleanEdited.matchAll(/pow\s*\(\s*\(\s*ai\.frame\s*-\s*lastSwitchFrame\s*\)\s*,\s*0\.9\s*\)/g)].length, 1);
    assert.equal([...cleanEdited.matchAll(/aiEconomyMgr\.metal\.current\s*\*\s*7\s*\)/g)].length, 1);

    // Ensure empty rule list restores exact original bytes.
    assert.equal(scripts.writeRecruitRules(editedFiles, []), factoryFile.text);

    // Ensure validateProfile introduces zero new errors or warnings on the edited profile.
    assert.deepEqual(config.validateProfile(editedFiles), config.validateProfile(files));
    const cleanProfile = [
        {path: "config/factory.json", text: '{"factory":{"armlab":{"unit":["armck"],"land":{"tier0":[1]}}}}'},
        {path: "script/manager/factory.as", text: 'namespace Factory {\nIUnitTask@ AiMakeTask(CCircuitUnit@ unit) { return null; }\nvoid AiTaskRemoved(IUnitTask@ task, bool done) {}\n}'}
    ];
    const editedClean = [
        cleanProfile[0],
        {path: "script/manager/factory.as", text: scripts.writeRecruitRules(cleanProfile, [fighter, bomber])}
    ];
    assert.deepEqual(config.validateProfile(editedClean), {errors: [], warnings: []});

    // Tamper protection and validation errors.
    const tampered = editedText.replace("Studio recruit: queued ", "Custom log: ");
    assert.throws(() => scripts.readRecruitRules(files.map(f => f.path === factoryFile.path ? {...f, text: tampered} : f)), /was edited/);
    const tamperedHook = editedText.replace("StudioRecruit::Release(task);", "StudioRecruit::Release(null);");
    assert.throws(() => scripts.readRecruitRules(files.map(f => f.path === factoryFile.path ? {...f, text: tamperedHook} : f)), /was edited/);

    assert.throws(() => scripts.writeRecruitRules(files, [{...fighter, unit: "armap"}]), /must differ/);
    assert.throws(() => scripts.writeRecruitRules(files, [{...fighter, target: 0}]), /target count/);
    assert.throws(() => scripts.writeRecruitRules(files, [fighter, fighter]), /Duplicate/);
    assert.throws(() => scripts.writeRecruitRules(files, [{...bomber, milestones: ["bad-id"]}]), /unit IDs/);
    assert.throws(() => scripts.writeRecruitRules(files, [{...bomber, enemyRole: "bad role"}]), /enemy role/);
});

test("writeRecruitRules preserves CRLF line endings when factory.as uses CRLF", () => {
    const crlfFactory = [
        "namespace Factory {",
        "IUnitTask@ AiMakeTask(CCircuitUnit@ unit)",
        "{",
        "\treturn aiFactoryMgr.DefaultMakeTask(unit);",
        "}",
        "void AiTaskRemoved(IUnitTask@ task, bool done)",
        "{",
        "}",
        "}"
    ].join("\r\n");
    const files = [{path: "script/manager/factory.as", text: crlfFactory}];
    const rule = {factory: "armap", unit: "armfig", target: 10};
    const edited = scripts.writeRecruitRules(files, [rule]);
    assert.doesNotMatch(edited, /[^\r]\n/);
    assert.equal(scripts.writeRecruitRules([{path: "script/manager/factory.as", text: edited}], []), crlfFactory);
});

test("real Hard and defensive-wk2 scripts parse cleanly with inspectScript", {
    skip: !["hard", "defensive-wk2"].every(profile => fs.existsSync(path.join(localProfiles, profile, "script"))) &&
        "Optional local Willtest profiles are unavailable"
}, () => {
    for (const profile of ["hard", "defensive-wk2"]) {
        const dir = path.join(localProfiles, profile);
        const files = readProfileFiles(dir).filter(f => f.path.endsWith(".as"));
        assert.ok(files.length > 0);
        for (const file of files) {
            const inspected = scripts.inspectScript(file.text);
            assert.ok(Array.isArray(inspected.constants));
            assert.ok(Array.isArray(inspected.hooks));
            assert.ok(Array.isArray(inspected.includes));
        }
    }
});

test("Hard profile scripts match all exposed scriptSettings patterns uniquely", {
    skip: !fs.existsSync(path.join(localProfiles, "hard", "script")) && "Optional local Willtest Hard profile is unavailable"
}, () => {
    const hardDir = path.join(localProfiles, "hard");
    const files = readProfileFiles(hardDir);
    const num = "(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?[fF]?";
    const checks = [
        ["script/manager/factory.as", [
            ["AiRandom\\s*\\(\\s*", "\\s*,"],
            ["AiRandom\\s*\\(\\s*\\d+\\s*,\\s*", "\\s*\\)\\s*\\*\\s*SECOND"],
            ["pow\\s*\\(\\s*\\(\\s*ai\\.frame\\s*-\\s*lastSwitchFrame\\s*\\)\\s*,\\s*", "\\s*\\)"],
            ["aiEconomyMgr\\.metal\\.current\\s*\\*\\s*", "\\s*\\)"]
        ]],
        ["script/manager/economy.as", [
            ["aiEconomyMgr\\.isMetalEmpty\\s*=\\s*metal\\.current\\s*<\\s*metal\\.storage\\s*\\*\\s*", "\\s*;"],
            ["aiEconomyMgr\\.isMetalFull\\s*=\\s*metal\\.current\\s*>\\s*metal\\.storage\\s*\\*\\s*", "\\s*;"],
            ["if\\s*\\(\\s*ai\\.frame\\s*<\\s*", "\\s*\\*\\s*MINUTE"],
            ["aiEconomyMgr\\.isEnergyStalling\\s*=\\s*\\(\\s*energy\\.income\\s*<\\s*energy\\.pull\\s*\\)\\s*&&\\s*\\(\\s*energy\\.current\\s*<\\s*energy\\.storage\\s*\\*\\s*", "\\s*\\)"],
            ["aiEconomyMgr\\.isEnergyEmpty\\s*=\\s*energy\\.current\\s*<\\s*energy\\.storage\\s*\\*\\s*", "\\s*;"],
            ["aiEconomyMgr\\.isEnergyStalling\\s*=\\s*aiEconomyMgr\\.isEnergyEmpty\\s*\\|\\|\\s*\\(\\s*\\(\\s*energy\\.income\\s*<\\s*energy\\.pull\\s*\\)\\s*&&\\s*\\(\\s*energy\\.current\\s*<\\s*energy\\.storage\\s*\\*\\s*", "\\s*\\)\\s*\\)"],
            ["aiEconomyMgr\\.isEnergyFull\\s*=\\s*energy\\.current\\s*>\\s*energy\\.storage\\s*\\*\\s*", "\\s*;"],
            ["aiFactoryMgr\\.isAssistRequired\\s*=\\s*\\(\\s*metal\\.current\\s*>\\s*metal\\.storage\\s*\\*\\s*", "\\s*\\)"]
        ]],
        ["script/main.as", [
            ["cdef\\.costM\\s*>=\\s*", "\\s*&&\\s*!cdef\\.IsMobile\\s*\\("]
        ]],
        ["script/manager/builder.as", [
            ["if\\s*\\(\\s*cdef\\.costM\\s*<\\s*", "\\s*\\)"]
        ]],
        ["script/manager/military.as", [
            ["ai\\.frame\\s*>\\s*", "\\s*\\*\\s*MINUTE"],
            ["aiEconomyMgr\\.metal\\.income\\s*>\\s*", "\\s*\\)"],
            ["aiEnemyMgr\\.mobileThreat\\s*>\\s*", "\\s*\\)"],
            ["GetEnemyThreat\\s*\\(\\s*Unit::Role::AA\\.type\\s*\\)\\s*<=\\s*", "\\s*;"]
        ]]
    ];
    for (const [relPath, patterns] of checks) {
        const clean = scripts.mask(files.find(f => f.path === relPath).text);
        for (const [prefix, suffix] of patterns) {
            const re = new RegExp(`(${prefix})(${num})(${suffix})`, "g");
            assert.equal([...clean.matchAll(re)].length, 1, `${relPath} -> ${prefix}`);
        }
    }
});
