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
        project: options.newProject ? [] : [{
            key: "fixture", name: "Fixture Project", history_error: "Fixture history unavailable",
            default_profile: "hard", profiles: [{key: "hard", name: "Hard"}]
        }],
        files: [{path: openingPath, text: commander, sha256: "fixture-original"}],
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
            game_capacity: {processor_cores: 8, parallel_games: 1}});
        if (pathname === "/api/profile") return json({files: fixture.files});
        if (pathname === "/api/match/cache") return json({content});
        if (pathname === "/api/match/status") return json(fixture.snapshot);
        if (pathname === "/api/match/history") return json({history: records});
        if (pathname === "/api/experiment/history") {
            const query = new URL(req.url, "http://localhost").searchParams;
            if (query.has("id")) return json({experiment: options.savedExperiment});
            return json({experiments: options.savedExperiment ? [
                {id: options.savedExperiment.id, mode: "tournament", state: "completed", started_at: "2026-09-24"}
            ] : [], next_offset: null});
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
        if (pathname === "/api/experiment/start") {
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
    const {page, requests} = await workshop(t);
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
    await page.getByRole("spinbutton", {name: "Schedule seed (optional)", exact: true}).fill("0");
    await page.getByRole("button", {name: "Start experiment", exact: true}).press("Enter");
    await page.getByText(/650 metal.*1,700 energy/).waitFor();
    const sent = requests.find(request => request.pathname === "/api/experiment/start")?.data;
    assert.equal(sent?.mode, "sweep");
    assert.equal(sent?.seed, 0);
    assert.deepEqual(sent?.start_options, startOptions);
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
