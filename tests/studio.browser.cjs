"use strict";

const assert = require("node:assert/strict");
const {readFile} = require("node:fs/promises");
const {createServer} = require("node:http");
const path = require("node:path");
const {test, before, after} = require("node:test");
const {chromium} = require(process.env.STUDIO_PLAYWRIGHT_MODULE || "playwright");

const staticRoot = path.resolve(__dirname, "../web/studio");
const commander = `SOpener@ GetOpenInfo() {
    return SOpener({
        {Factory::armlab, array<SQueue> = {SQueue(1.0f, {SO(RT::BUILDER, 1)})}},
        {Factory::corlab, array<SQueue> = {SQueue(1.0f, {SO(RT::SCOUT, 2)})}}
    }, {SO(RT::BUILDER)});
}`;
const openingPath = "script/misc/commander.as";
const startOptions = {starting_metal: 650, starting_energy: 1700, starter_base: false, build_turret: true};
const settings = {bar_root: "Fixture BAR", engine: "Fixture Engine"};
const content = {
    games: ["Fixture BAR"],
    maps: [{name: "Fixture Map", width: 512, height: 512}],
    ais: [{short_name: "fixture", name: "Fixture AI", version: "1",
        profiles: [{key: "hard", name: "Hard"}, {key: "easy", name: "Easy"}]}],
    warnings: []
};
let browser;

before(async () => {
    browser = await chromium.launch({executablePath: process.env.STUDIO_CHROMIUM_PATH || undefined});
});
after(async () => { await browser?.close(); });

async function workshop(t, options = {}) {
    const errors = [], requests = [];
    const fixture = {
        snapshot: {state: "idle"},
        preferences: options.preferences,
        project: options.newProject ? [] : [{
            key: "fixture", name: "Fixture Project", history_error: "Fixture history unavailable",
            default_profile: "hard", profiles: options.profiles ?? [{key: "hard", name: "Hard"}]
        }],
        files: options.files ?? [{path: openingPath, text: commander, sha256: "fixture-original"}],
    };
    const records = [{kind: "match", state: "completed", run_dir: "match-fixture", map: "Fixture Map",
        outcome: "timeout", players: [], start_options: startOptions}];
    const lines = ["==== <Startup> ====", "Build commander", "==== </Startup> ====", "Needle A", "Needle B"];
    const server = createServer(async (req, res) => {
        const pathname = new URL(req.url, "http://localhost").pathname;
        let data;
        if (req.method === "POST") {
            let body = "";
            for await (const chunk of req) body += chunk;
            data = JSON.parse(body);
            requests.push({pathname, data});
        }
        const json = (value, code = 200) => {
            res.writeHead(code, {"Content-Type": "application/json"});
            res.end(JSON.stringify(value));
        };
        if (pathname === "/api/state") return json({projects: fixture.project,
            project_issues: [{project: "broken-project", message: "Fixture project metadata failed."}],
            settings: options.newProject ? null : settings, default_bar_root: settings.bar_root,
            preferences: fixture.preferences,
            game_capacity: {processor_cores: 8, parallel_games: 1}});
        if (pathname === "/api/preferences") {
            fixture.preferences = {...fixture.preferences, ...data};
            return json({ok: true});
        }
        if (pathname === "/api/profile") return json({files: fixture.files});
        if (pathname === "/api/match/cache") return json({content: options.content ?? content});
        if (pathname === "/api/match/status") return json(fixture.snapshot);
        if (pathname === "/api/match/history") return json({history: records});
        if (pathname === "/api/experiment/history") {
            const query = new URL(req.url, "http://localhost").searchParams;
            const saved = fixture.savedExperiments ??= options.savedExperiments ?? (options.savedExperiment ? [options.savedExperiment] : []);
            if (query.has("id")) return json({experiment: saved.find(item => item.id === query.get("id"))});
            return json({experiments: saved.map(item => (
                {id: item.id, mode: "tournament", state: "completed", started_at: item.started_at ?? "2026-09-24"}
            )), next_offset: null});
        }
        if (pathname === "/api/experiment/delete") {
            fixture.savedExperiments = data.all ? [] : fixture.savedExperiments.filter(item => item.id !== data.id);
            if (data.all || data.id === fixture.snapshot.id) fixture.snapshot = {state: "idle"};
            return json({removed: 1});
        }
        if (pathname === "/api/match/start") return json({error: "Fixture queue rejected this setup."}, 400);
        if (pathname === "/api/installations/managed") return json({issues: [], installations: fixture.installations ??= [
            {project: "retired", name: "Retired AI", engine: "Older Engine", path: "Fixture AI directory"}
        ]});
        if (pathname === "/api/remove") {
            fixture.installations = [];
            return json({});
        }
        if (pathname === "/api/storage" && req.method === "GET") {
            const runs = fixture.runs ??= [
                {id: "match-recorded", bytes: 100, pinned: false, has_replay: true, protected: true},
                {id: "match-expired", bytes: 200, pinned: false, has_replay: false, protected: false}
            ];
            return json({runs, total_runs: runs.length, total_bytes: runs.reduce((sum, run) => sum + run.bytes, 0),
                protected_runs: runs.filter(run => run.protected).length, next_offset: null});
        }
        if (pathname === "/api/storage" && data.action === "cleanup") {
            fixture.runs = fixture.runs.filter(run => !data.ids.includes(run.id));
            return json({removed: data.ids});
        }
        if (pathname === "/api/sources") return json({error: "Fixture installation could not be inspected."}, 400);
        if (pathname === "/api/match/logs") return json({offset: 0, next_offset: null, ends_with_newline: true,
            lines: lines.map(text => ({text: "[t=00:00:01.000] [f=1] " + text, category: "other"}))});
        if (pathname === "/api/save") {
            fixture.files = data.files.map(file => ({...file, sha256: "fixture-saved"}));
            return json({files: fixture.files});
        }
        if (pathname === "/api/experiment/literal") {
            const source = fixture.files.find(file => file.path === data.path);
            const text = source?.text.split(/\r\n?|\n/)[data.line - 1] ?? "";
            const tokens = [...text.matchAll(/\d+(?:\.\d+)?f?/g)];
            const result = tokens.find(token => data.column - 1 >= token.index && data.column - 1 < token.index + token[0].length);
            return result ? json({column: result.index + 1, length: result[0].length}) : json({error: "Select a numeric literal."}, 400);
        }
        if (pathname === "/api/experiment/start") {
            if (options.experimentStartError) return json({error: options.experimentStartError}, 400);
            fixture.snapshot = {id: "experiment-fixture", operation: "experiment", state: "running",
                experiment: {id: "experiment-fixture", mode: "sweep", minutes: 1, games: [],
                    scheduled_games: 2, completed_games: 0, start_options: data.start_options},
                active_games: 1, parallel_games: 1};
            return json(fixture.snapshot);
        }
        if (pathname.startsWith("/api/")) return json({});
        if (pathname === "/data/units.json") return json({units: {names: {}}});
        if (pathname === "/session.js") {
            res.writeHead(200, {"Content-Type": "text/javascript"});
            return res.end('window.WORKSHOP_TOKEN = "fixture";');
        }
        const file = path.join(staticRoot, pathname === "/" ? "index.html" : pathname);
        const type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[path.extname(file)];
        try {
            const body = await readFile(file);
            res.writeHead(200, {"Content-Type": type});
            return res.end(body);
        } catch (error) {
            res.writeHead(error.code === "ENOENT" ? 404 : 500);
            return res.end();
        }
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    t.after(() => new Promise(resolve => server.close(resolve)));
    const context = await browser.newContext({permissions: ["clipboard-read", "clipboard-write"]});
    t.after(async () => { await context.close(); assert.deepEqual(errors, []); });
    const page = await context.newPage();
    page.setDefaultTimeout(4500);
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.getByRole("heading", {name: "Overview", exact: true}).waitFor();
    return {fixture, page, requests};
}

const isFocused = locator => locator.evaluate(node => node === document.activeElement);
const nav = (page, name) => page.getByRole("navigation").getByRole("button", {name});

test("production explains the income score beside the tier cutoffs", async t => {
    const files = [{path: "config/factory.json", text: JSON.stringify({factory: {armlab: {
        income_tier: [10], unit: ["armflea"], land: {tier0: [1], tier1: [2]}
    }}}), sha256: "fixture-factory"}];
    const {page} = await workshop(t, {files});
    await nav(page, "Production").click();
    await page.getByText("Income score = min(average metal income, average energy income) × economy factor", {exact: true}).waitFor();
    await page.getByText("Minimum score for Tier 1", {exact: true}).waitFor();
});

test("placement redraws the building area, clearance and offset and preserves a custom shape", async t => {
    const files = [{path: "config/block_map.json", text: JSON.stringify({building: {class_land: {
        fac_land_t1: {type: ["custom", "factory"], size: [8, 8], yard: [8, 10], offset: [0, 0], radius: 6}
    }}}), sha256: "fixture-placement"}];
    const {page, requests} = await workshop(t, {files});
    await nav(page, "Building placement").click();
    await page.getByText(/Spacing area and building category.*2/).click();
    const selection = page.getByRole("combobox", {name: "Area shape", exact: true});
    assert.equal(await selection.inputValue(), "custom");
    await selection.selectOption("rectangle");
    const preview = page.locator(".placement-preview");
    const footprint = preview.locator(".placement-footprint");
    const spacing = preview.locator(".placement-clearance");
    const initialFootprint = await footprint.boundingBox();
    const initialSpacing = await spacing.boundingBox();
    await page.locator("summary").filter({hasText: /^Building area size\s+·/}).click();
    await page.getByRole("spinbutton", {name: "Width", exact: true}).fill("10");
    assert.ok((await footprint.boundingBox()).width > initialFootprint.width);
    await page.locator("summary").filter({hasText: /^Extra clearance\s+·/}).click();
    await page.getByRole("spinbutton", {name: "Width of extra clearance"}).fill("12");
    await page.getByRole("spinbutton", {name: "Width of extra clearance"}).press("Tab");
    assert.ok((await spacing.boundingBox()).width > initialSpacing.width);
    const beforeOffset = await spacing.boundingBox();
    await page.locator("summary").filter({hasText: /^Position adjustment\s+·/}).click();
    await page.getByRole("spinbutton", {name: "Left/right shift"}).fill("3");
    await page.getByRole("spinbutton", {name: "Left/right shift"}).press("Tab");
    assert.ok((await spacing.boundingBox()).x > beforeOffset.x);
    await selection.selectOption("circle");
    assert.equal(await spacing.evaluate(node => getComputedStyle(node).borderRadius), "50%");
    await selection.selectOption("custom");
    assert.equal(await spacing.count(), 0);
    await page.getByRole("button", {name: "Save changes", exact: true}).click();
    const saved = requests.find(request => request.pathname === "/api/save")?.data;
    const rule = JSON.parse(saved.files[0].text).building.class_land.fac_land_t1;
    assert.deepEqual(rule, {type: ["custom", "factory"], size: [10, 8], yard: [12, 10], offset: [3, 0], radius: 6});
});

test("distance tooltips follow live edits and compare converted distances using friendly names", async t => {
    const files = [
        {path: "config/economy.json", text: '{"economy":{"cluster_range":20}}', sha256: "fixture-economy"},
        {path: "config/block_map.json", text: JSON.stringify({building: {class_land: {
            fac_land_t1: {type: ["rectangle", "factory"], size: [6, 8], yard: [2, 2], offset: [0, 0]}
        }}}), sha256: "fixture-placement"},
    ];
    const {page} = await workshop(t, {files, content: {...content, maps: [
        {name: "Fixture Map", width: 4650, height: 12288},
    ]}});
    const tip = page.locator(".control-tooltip:visible");
    await nav(page, "Economy").click();
    const range = page.getByRole("spinbutton", {name: "Metal cluster range", exact: true});
    await range.hover();
    await tip.filter({hasText: /About the width of a Grunt .*20 elmos/}).waitFor();
    await range.fill("4650");
    await tip.filter({hasText: /Basilica long-range plasma cannon .*4,650 elmos/}).waitFor();
    await range.press("Escape");
    await tip.waitFor({state: "hidden"});
    await nav(page, "Building placement").click();
    const width = page.locator(".placement-footprint-label .distance-value").first();
    await width.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    assert.equal(await isFocused(width), true);
    assert.match(await width.getAttribute("aria-description"), /width of a Juggernaut/);
    assert.equal(await width.evaluate(node => node.matches(":focus-visible")), true);
    await tip.filter({hasText: /width of a Juggernaut .*96 elmos/}).waitFor();
    await nav(page, "Test match").click();
    await page.getByRole("combobox", {name: "Primary map", exact: true}).hover();
    await tip.filter({hasText: /Width.*Basilica.*Height.*Supreme Isthmus.*12,288 elmos/}).waitFor();
    await nav(page, "Tournaments & sweeps").click();
    await page.locator(".experiment-map-list .distance-value:visible").filter({hasText: "Fixture Map"}).hover();
    await tip.filter({hasText: /Width.*Basilica.*Height.*Supreme Isthmus.*12,288 elmos/}).waitFor();
    await page.getByRole("combobox", {name: "Experiment type"}).selectOption("sweep");
    const source = files[0].text;
    await page.getByRole("combobox", {name: "Source file"}).selectOption(files[0].path);
    await page.getByRole("spinbutton", {name: "Literal column"}).fill(String(source.indexOf("20") + 2));
    const literal = page.locator(".literal-token.distance-value");
    await page.getByText("Each candidate replaces the full number 20.").waitFor();
    assert.match(await literal.getAttribute("aria-description"), /About the width of a Grunt .*20 elmos/);
    await literal.scrollIntoViewIfNeeded();
    await literal.hover();
    await tip.filter({hasText: /About the width of a Grunt .*20 elmos/}).waitFor();
});

test("first-run lookup error is announced inside its dialog", async t => {
    const {page} = await workshop(t, {newProject: true});
    await page.getByRole("heading", {name: "Create your first AI"}).waitFor();
    await page.locator("#new-project").press("Enter");
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", {name: "Find installed game versions"}).press("Enter");
    await dialog.getByRole("status").filter({hasText: "Fixture installation could not be inspected."}).waitFor();
});

test("keyboard opening edits preserve focus and save to the fixture profile", async t => {
    const {page, requests} = await workshop(t);
    await nav(page, "Opening").press("Enter");
    const factory = page.getByRole("combobox", {name: "Factory", exact: true});
    await factory.press("ArrowDown");
    assert.equal(await factory.inputValue(), "corlab");
    assert.equal(await isFocused(factory), true);
    const counts = page.getByRole("spinbutton", {name: "Count", exact: true});
    await page.getByRole("button", {name: "Add step", exact: true}).first().press("Enter");
    assert.equal(await isFocused(counts.last()), true);
    await page.getByRole("button", {name: "Save changes", exact: true}).press("Enter");
    await page.getByText("Profile changes saved.", {exact: false}).waitFor();
    const saved = requests.find(request => request.pathname === "/api/save")?.data;
    assert.equal(saved?.profile, "hard");
    assert.equal(saved.files[0].sha256, "fixture-original");
    assert.match(saved.files[0].text, /RT::SCOUT, 2\), SO\(RT::BUILDER\)/);
});

test("adding and removing a roster slot leaves focus with the team", async t => {
    const {page} = await workshop(t);
    await nav(page, "Test match").press("Enter");
    await page.getByRole("button", {name: /Add AI/}).first().press("Enter");
    const second = page.getByRole("combobox", {name: "Side 1 · Your team AI 2", exact: true});
    assert.equal(await isFocused(second), true);
    await page.getByRole("button", {name: "Remove Side 1 · Your team AI 2"}).press("Enter");
    assert.equal(await isFocused(page.getByRole("combobox", {name: "Side 1 · Your team AI 1", exact: true})), true);
});

test("log folding and inclusive range copying work with the keyboard", async t => {
    const {page} = await workshop(t);
    await nav(page, "Test match").press("Enter");
    await page.getByRole("button", {name: "View logs", exact: true}).press("Enter");
    await page.getByRole("button", {name: /lines folded/}).press("Enter");
    assert.equal(await isFocused(page.getByRole("button", {name: "▾ fold", exact: true})), true);
    await page.getByRole("button", {name: "Select lines", exact: true}).press("Enter");
    await page.getByRole("group", {name: "Log line range"}).getByRole("spinbutton", {name: "First line"}).fill("4");
    await page.getByRole("group", {name: "Log line range"}).getByRole("spinbutton", {name: "Last line"}).fill("5");
    await page.getByRole("button", {name: "Select range"}).press("Enter");
    await page.getByRole("button", {name: "Copy selected"}).press("Enter");
    await page.getByRole("status").filter({hasText: "Copied 2 lines."}).waitFor();
    assert.equal((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n"),
        "4  [t=00:00:01.000] [f=1] Needle A\n5  [t=00:00:01.000] [f=1] Needle B");
});

test("live progress retains focus on the experiment cancel control", async t => {
    const {fixture, page} = await workshop(t);
    fixture.snapshot = {id: "experiment-fixture", state: "running", operation: "experiment",
        active_games: 1, parallel_games: 1, experiment: {mode: "tournament", id: "experiment-fixture",
            minutes: 1, completed_games: 0, scheduled_games: 2, games: [], start_options: startOptions}};
    await nav(page, "Tournaments & sweeps").press("Enter");
    const cancel = page.getByRole("button", {name: "Cancel experiment"});
    await cancel.focus();
    fixture.snapshot.experiment.completed_games = 1;
    fixture.snapshot.experiment.games.push({index: 1, outcome: "timeout", map: "Fixture Map"});
    await page.getByRole("status").filter({hasText: "1 / 2 games"}).waitFor();
    assert.equal(await isFocused(cancel), true);
    await page.getByText(/650 metal.*1,700 energy/).waitFor();
});

test("deleting a selected result keeps the other result until delete all is confirmed", async t => {
    const {page, requests} = await workshop(t, {savedExperiments: [
        {id: "first", started_at: "2026-09-25"}, {id: "second", started_at: "2026-09-24"},
    ]});
    await nav(page, "Tournaments & sweeps").click();
    const saved = page.getByRole("combobox", {name: "Saved experiment"});
    await saved.selectOption("second");
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", {name: "Delete selected result"}).click();
    await page.getByRole("option", {name: /2026-09-24/}).waitFor({state: "detached"});
    assert.equal(await saved.inputValue(), "first");
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", {name: "Delete all results"}).click();
    await page.getByText("No saved experiments.").waitFor();
    assert.deepEqual(requests.filter(request => request.pathname === "/api/experiment/delete").map(request => request.data),
        [{id: "second"}, {all: true}]);
});

test("new tournament and sweep default to the highest installed BAR build", async t => {
    const legacy = "Beyond All Reason test-9999-abcdef0";
    const latest = "Beyond All Reason test-10010-0abcdef";
    const {page} = await workshop(t, {
        content: {...content, games: [legacy, "Beyond All Reason $VERSION", latest]},
        preferences: {tournament: {game: legacy}},
    });
    await nav(page, "Tournaments & sweeps").click();
    const game = page.getByRole("combobox", {name: "Game", exact: true});
    assert.equal(await game.inputValue(), latest);
    await page.getByRole("combobox", {name: "Experiment type"}).selectOption("sweep");
    assert.equal(await game.inputValue(), latest);
});

test("managed installation removal targets the selected engine for a deleted project", async t => {
    const {page, requests} = await workshop(t);
    await nav(page, "AI install / remove").press("Enter");
    await page.getByText("Fixture project metadata failed.", {exact: false}).waitFor();
    await page.getByRole("combobox", {name: "Managed installation"}).selectOption({label: "Retired AI (retired) · Older Engine"});
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", {name: "Remove managed installation"}).press("Enter");
    await page.getByRole("status").filter({hasText: "Retired AI removed"}).waitFor();
    assert.deepEqual(requests.find(request => request.pathname === "/api/remove")?.data, {
        project: "retired", engine: "Older Engine", bar_root: "Fixture BAR", confirmations: []
    });
});

test("storage cleanup selects unprotected runs and refreshes usage", async t => {
    const {page, requests} = await workshop(t);
    await nav(page, "Test match").press("Enter");
    await page.getByRole("button", {name: "Artifact storage"}).press("Enter");
    const recorded = page.getByRole("checkbox", {name: "Select match-recorded for cleanup"});
    const expired = page.getByRole("checkbox", {name: "Select match-expired for cleanup"});
    assert.equal(await recorded.isDisabled(), true);
    await expired.check();
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", {name: "Delete selected artifacts"}).press("Enter");
    await page.getByRole("status").filter({hasText: /1 run.*100 B/}).waitFor();
    assert.deepEqual(requests.find(request => request.pathname === "/api/storage")?.data,
        {action: "cleanup", ids: ["match-expired"]});
});

test("sweep submits its own starting conditions and an optional schedule seed", async t => {
    const {page, requests} = await workshop(t, {files: [{path: "script/tuning.as", text: "const float delay = 150.0f;", sha256: "digest"}]});
    await nav(page, "Tournaments & sweeps").press("Enter");
    await page.getByRole("spinbutton", {name: "Starting metal", exact: true}).fill("1200");
    const mode = page.getByRole("combobox", {name: "Experiment type"});
    await mode.selectOption("sweep");
    const metal = page.getByRole("spinbutton", {name: "Starting metal", exact: true});
    assert.equal(await metal.inputValue(), "2000");
    await metal.fill("650");
    await page.getByRole("spinbutton", {name: "Starting energy", exact: true}).fill("1700");
    await page.getByRole("checkbox", {name: "Starter base", exact: true}).uncheck();
    await page.getByRole("textbox", {name: "Candidate values (2–16)", exact: true}).fill("1, 2");
    await page.getByRole("combobox", {name: "Detected script parameter (optional)"}).selectOption("0");
    await page.getByRole("spinbutton", {name: "Schedule seed (optional)", exact: true}).fill("0");
    await page.getByRole("button", {name: "Start experiment", exact: true}).press("Enter");
    await page.getByText(/650 metal.*1,700 energy/).waitFor();
    const sent = requests.find(request => request.pathname === "/api/experiment/start")?.data;
    assert.equal(sent?.mode, "sweep");
    assert.equal(sent?.seed, 0);
    assert.equal(sent?.sweep.column, 21);
    assert.deepEqual(sent?.start_options, startOptions);
});

test("sweep preview shows the entered column and the full selected literal across workspace changes", async t => {
    const {page, requests} = await workshop(t, {
        files: [{path: "script/tuning.as", text: "// Tunable wait\nconst float delay = 150.0f;", sha256: "digest"}],
        profiles: [{key: "hard", name: "Hard"}, {key: "easy", name: "Easy"}],
        experimentStartError: "The selected engine is unavailable.",
    });
    await nav(page, "Tournaments & sweeps").press("Enter");
    const mode = page.getByRole("combobox", {name: "Experiment type"});
    await mode.selectOption("sweep");
    await page.getByRole("combobox", {name: "Detected script parameter (optional)"}).selectOption("0");
    const line = page.getByRole("spinbutton", {name: "Literal line"});
    const column = page.getByRole("spinbutton", {name: "Literal column"});
    assert.equal(await line.inputValue(), "2");
    assert.equal(await column.inputValue(), "21");
    await column.fill("23");
    await page.getByText("Each candidate replaces the full number 150.0f.").waitFor();
    assert.equal(await page.locator(".literal-token").textContent(), "150.0f");
    assert.equal(await page.locator(".literal-column").textContent(), "0");

    const candidates = page.getByRole("textbox", {name: "Candidate values (2–16)"});
    const wallTime = page.getByRole("spinbutton", {name: "Real-time limit (minutes)"});
    await candidates.fill("20, 40");
    await wallTime.fill("1.25");
    await page.locator("#profile").selectOption("easy");
    await page.getByText(/Source profile Fixture Project \/ easy/).waitFor();
    assert.equal(await mode.inputValue(), "sweep");
    assert.equal(await candidates.inputValue(), "20, 40");
    assert.equal(await wallTime.inputValue(), "1.25");
    await page.locator("#profile").selectOption("hard");
    await page.getByText(/Source profile Fixture Project \/ hard/).waitFor();
    assert.equal(await line.inputValue(), "2");
    assert.equal(await column.inputValue(), "23");
    const start = page.getByRole("button", {name: "Start experiment", exact: true});
    await start.press("Enter");
    await page.getByRole("status").filter({hasText: "Could not start the experiment. The selected engine is unavailable."}).waitFor();
    assert.equal(requests.find(request => request.pathname === "/api/experiment/start")?.data.seconds_limit, 75);
    assert.equal(await isFocused(start), true);
});

test("both map pools survive reload and the tournament explains its short game budget", async t => {
    const {page} = await workshop(t, {content: {...content, maps: [...content.maps, {name: "Second Map"}]}});
    await nav(page, "Tournaments & sweeps").press("Enter");
    await page.getByRole("checkbox", {name: "Fixture AI · Hard"}).check();
    await page.getByRole("checkbox", {name: "Fixture AI · Easy"}).check();
    await page.getByRole("button", {name: "Add Second Map", exact: true}).click();
    await page.getByRole("spinbutton", {name: "Game limit", exact: true}).fill("2");
    await page.getByText(/Full coverage needs 4 games.*leaves 2 unscheduled/).waitFor();
    const mode = page.getByRole("combobox", {name: "Experiment type"});
    await mode.selectOption("sweep");
    const saved = page.waitForResponse(response => response.url().endsWith("/api/preferences") &&
        response.request().postDataJSON()?.sweep?.maps?.length === 0);
    await page.getByRole("button", {name: "Remove Fixture Map", exact: true}).click();
    await saved;
    await page.reload();
    await nav(page, "Tournaments & sweeps").press("Enter");
    await page.getByRole("button", {name: "Remove Second Map", exact: true}).waitFor();
    await page.getByText(/Full coverage needs 4 games.*leaves 2 unscheduled/).waitFor();
    await mode.selectOption("sweep");
    await page.getByRole("heading", {name: "Selected maps (0/32)"}).waitFor();
});

test("a running experiment hides setup and then opens its completed result", async t => {
    const oldResult = {id: "older-result", mode: "tournament", seed: 123};
    const {page, fixture} = await workshop(t, {savedExperiment: oldResult});
    const id = "completing-experiment";
    const experiment = {id, mode: "tournament", minutes: 1, completed_games: 0, scheduled_games: 2, games: [], seed: 987};
    fixture.snapshot = {id, state: "running", operation: "experiment", active_games: 1, parallel_games: 1, experiment};
    await nav(page, "Tournaments & sweeps").press("Enter");
    await page.getByText("New experiment setup returns when the current operation ends.").waitFor();
    assert.equal(await page.getByRole("heading", {name: "New experiment"}).isVisible(), false);
    fixture.savedExperiments = [{...experiment, completed_games: 2}, oldResult];
    fixture.snapshot = {...fixture.snapshot, state: "completed", message: "Experiment complete.", result: {experiment: fixture.savedExperiments[0]}};
    await page.locator("#experiment-history").getByText(/Schedule seed 987/).waitFor();
    assert.equal(await page.getByRole("combobox", {name: "Saved experiment"}).inputValue(), id);
    await page.getByRole("heading", {name: "New experiment"}).waitFor();
});

test("an experiment failure before setup completes is visible on the page", async t => {
    const {page, fixture} = await workshop(t);
    fixture.snapshot = {id: "failed-experiment", state: "failed", operation: "experiment",
        message: "Could not create the experiment workspace."};
    await nav(page, "Tournaments & sweeps").press("Enter");
    await page.getByRole("status").filter({hasText: "Experiment failed. Could not create the experiment workspace."}).waitFor();
});

test("queue errors remain next to the keyboard submit control", async t => {
    const {page} = await workshop(t);
    await nav(page, "Test match").press("Enter");
    const queue = page.getByRole("button", {name: "Queue match"});
    await queue.press("Enter");
    await page.getByRole("status").filter({hasText: "Fixture queue rejected this setup."}).waitFor();
    assert.equal(await isFocused(queue), true);
});

test("a saved tournament loads its exact schedule from the paged summary", async t => {
    const {page} = await workshop(t, {savedExperiment: {
        id: "fixture-saved", mode: "tournament", seed: 4321, start_options: startOptions,
        coverage: {completed_pairs: 1, scheduled_pairs: 2, full_pairs: 3},
        schedule: [{index: 1, pair_index: 1, map: "Fixture map", players: [
            {short_name: "Fixture AI", profile: "hard", faction: "Legion", ally: 0},
            {short_name: "Other AI", profile: "easy", faction: "Cortex", ally: 1}
        ]}],
        provenance: {engine_sha256: "fixture-engine-digest"},
    }});
    await nav(page, "Tournaments & sweeps").press("Enter");
    await page.getByText("Schedule seed 4321", {exact: true}).waitFor();
    await page.getByText("Scheduled games", {exact: true}).press("Enter");
    await page.getByRole("cell", {name: "Fixture AI/hard (Legion)", exact: true}).waitFor();
    await page.getByText("Engine and content fingerprints", {exact: true}).press("Enter");
    await page.getByText("fixture-engine-digest", {exact: true}).waitFor();
});
