"use strict";

function experimentNumber(label, value, min, max) {
    const wrap = element("label", "field", label), input = element("input");
    Object.assign(input, {type: "number", value, min, max, step: "1", required: true});
    wrap.append(input);
    return [wrap, input];
}

function tournamentProfiles(choices, savedPlayers, changed) {
    const details = element("details", "experiment-choices");
    details.open = true;
    const options = new Map(choices);
    const selected = new Map(savedPlayers.map(({ai, faction}) => [JSON.stringify(ai), faction])
        .filter(([key]) => options.has(key)));
    const summary = element("summary");
    const list = element("div", "tournament-profiles");
    const checks = [];
    details.append(summary, list);
    function updateCount() {
        summary.textContent = `Tournament profiles (${selected.size}/16)`;
        for (const input of checks) input.disabled = !input.checked && selected.size >= 16;
    }
    for (const [key, text] of choices) {
        const row = element("div", "tournament-profile");
        const label = element("label", "tournament-profile-choice"), input = element("input");
        input.type = "checkbox";
        input.checked = selected.has(key);
        checks.push(input);
        const faction = dropdown("Faction", tournamentFactionOptions, selected.get(key) || "Armada", value => {
            selected.set(key, value);
            changed();
        });
        const select = faction.querySelector("select");
        select.setAttribute("aria-label", `Faction for ${text}`);
        faction.hidden = !input.checked;
        input.addEventListener("change", () => {
            faction.hidden = !input.checked;
            if (input.checked) {
                select.value = selected.size % 2 === 0 ? "Armada" : "Cortex";
                selected.set(key, select.value);
            } else {
                selected.delete(key);
            }
            updateCount();
            changed();
        });
        label.append(input, document.createTextNode(text));
        row.append(label, faction);
        list.append(row);
    }
    updateCount();
    details.append(element("p", "muted tournament-faction-help",
        "Select 2–16 profiles. Rotate cycles through Armada, Cortex, and Legion. Random rolls independently each game."));
    return [details, selected];
}

const tournamentFactionOptions = [
    ["Armada", "Armada (fixed)"], ["Cortex", "Cortex (fixed)"], ["Legion", "Legion (fixed)"],
    ["Rotate", "Rotate"], ["Random", "Random"],
];

function mapSearchKey(name) {
    return name.toLowerCase().trim().replace(/[\s_]+/g, " ");
}

function mapRevision(name) {
    const key = mapSearchKey(name);
    const match = key.match(/^(.+?) v?(\d+(?:\.\d+)*)([a-z]?)$/);
    return match ? {family: match[1], parts: match[2].split(".").map(Number), suffix: match[3]}
        : {family: key, parts: [], suffix: ""};
}

function compareMapRevisions(a, b) {
    if (!a.parts.length || !b.parts.length) return a.parts.length - b.parts.length;
    for (let i = 0; i < Math.max(a.parts.length, b.parts.length); i++) {
        const difference = (a.parts[i] ?? 0) - (b.parts[i] ?? 0);
        if (difference) return difference;
    }
    return a.suffix.localeCompare(b.suffix);
}

function tournamentMapPool(maps, preferred, savedMaps) {
    const latest = new Map();
    for (const map of maps) {
        const revision = mapRevision(map.name);
        const current = latest.get(revision.family);
        const order = current ? compareMapRevisions(revision, current.revision) : 1;
        if (order > 0 || (order === 0 && map.name === preferred)) {
            latest.set(revision.family, {map, revision});
        }
    }
    const available = Array.from(latest.values(), entry => entry.map);
    const primary = latest.get(mapRevision(preferred).family)?.map.name || available[0]?.name;
    const selected = savedMaps
        ? savedMaps.map(name => latest.get(mapRevision(name).family)?.map.name).filter(Boolean)
        : [primary].filter(Boolean);
    return {available, selected};
}

const experimentMapLimit = 32;

function experimentMapPicker(maps, initial, changed = () => {}) {
    const picker = element("div", "experiment-map-picker");
    const selected = new Set(initial);
    const [searchLabel, search] = textInput("Search installed maps", "", "Filter by map name");
    search.type = "search";
    const columns = element("div", "experiment-map-columns");
    const catalog = element("div"), pool = element("div");
    const results = element("ul", "experiment-map-list");
    const picks = element("ul", "experiment-map-list");
    const resultMessage = element("p", "muted");
    const poolMessage = element("p", "muted", "Add at least one map to start.");
    const count = element("h3");
    count.setAttribute("aria-live", "polite");
    catalog.append(element("h3", "", "Available maps"), results, resultMessage);
    pool.append(count, picks, poolMessage);
    columns.append(catalog, pool);
    picker.append(element("h3", "", "Map pool"), searchLabel, columns);

    function mapRow(list, name, action, change, disabled = false) {
        const row = element("li");
        const control = button(action, () => {
            const index = Array.from(list.children).indexOf(row);
            change(name);
            draw();
            changed();
            const next = list.children[Math.min(index, list.children.length - 1)]?.lastElementChild;
            (next && !next.disabled ? next : search).focus({preventScroll: true});
        });
        control.setAttribute("aria-label", `${action} ${name}`);
        control.disabled = disabled;
        row.append(element("span", "", name), control);
        list.append(row);
    }

    function draw() {
        const query = mapSearchKey(search.value);
        const atLimit = selected.size >= experimentMapLimit;
        const matches = maps.filter(map => !selected.has(map.name) && mapSearchKey(map.name).includes(query));
        count.textContent = `Selected maps (${selected.size}/${experimentMapLimit})`;
        resultMessage.hidden = matches.length > 0;
        resultMessage.textContent = query ? "No available maps match your search." : "All maps are selected.";
        poolMessage.hidden = selected.size > 0;
        results.replaceChildren();
        picks.replaceChildren();
        for (const {name} of matches) mapRow(results, name, "Add", value => selected.add(value), atLimit);
        for (const name of selected) mapRow(picks, name, "Remove", value => selected.delete(value));
    }

    search.addEventListener("input", () => {
        results.scrollTop = 0;
        draw();
    });
    draw();
    return [picker, selected];
}

function experimentPlayer(choice, ally, faction = ally === 0 ? "Armada" : "Cortex") {
    const [short_name, version, profile] = JSON.parse(choice);
    return {short_name, version, profile, faction, ally};
}

function experimentForm(content) {
    const form = element("form", "panel"), fields = element("fieldset", "experiment-form");
    form.append(fields);
    const row = element("div", "row");
    const saved = state.experiments.tournament;
    const defaultGame = content.games.includes(state.match.game) ? state.match.game : content.games[0];
    const defaults = {game: defaultGame, minutes: Math.min(state.match.minutes, 120), game_limit: 24, seconds_limit: 1200};
    const setups = {tournament: {...defaults, ...saved}, sweep: {...defaults}};
    if (!content.games.includes(setups.tournament.game)) setups.tournament.game = defaultGame;
    let mode = "tournament";
    const tournament = tournamentMapPool(content.maps, state.match.map, saved?.maps);
    const [tournamentPicker, tournamentMaps] = experimentMapPicker(tournament.available, tournament.selected, saveTournament);
    const primaryMap = content.maps.find(map => map.name === state.match.map) || content.maps[0];
    const [sweepPicker, sweepMaps] = experimentMapPicker(content.maps, [primaryMap.name]);
    const choices = content.ais.flatMap(ai => (ai.profiles.length ? ai.profiles : [{key: "", name: "Default"}]).map(
        profile => [JSON.stringify([ai.short_name, ai.version, profile.key]), `${ai.name} · ${profile.name}`]
    ));
    const [candidateList, candidates] = tournamentProfiles(choices, saved?.players || [], saveTournament);
    const [minutesLabel, minutes] = experimentNumber("Game time limit (minutes)", setups.tournament.minutes, 1, 1193046);
    const [gamesLabel, games] = experimentNumber("Game limit", setups.tournament.game_limit, 1, 100);
    const [secondsLabel, seconds] = experimentNumber("Real-time limit (seconds)", setups.tournament.seconds_limit, 1, 21600);
    const limits = {minutes, game_limit: games, seconds_limit: seconds};
    function saveTournament() {
        if (mode !== "tournament") return;
        const {game, minutes, game_limit, seconds_limit} = setups.tournament;
        const tournament = {game, minutes, game_limit, seconds_limit, maps: [...tournamentMaps],
            players: Array.from(candidates, ([key, faction]) => ({ai: JSON.parse(key), faction}))};
        state.experiments.tournament = tournament;
        rememberPreference({tournament});
    }
    for (const [key, input] of Object.entries(limits)) {
        input.addEventListener("input", () => {
            if (!input.validity.valid || setups[mode][key] === Number(input.value)) return;
            setups[mode][key] = Number(input.value);
            saveTournament();
        });
    }
    const sweepFields = element("fieldset", "experiment-form");
    const files = state.files.filter(file => /\.(?:as|json)$/i.test(file.path));
    let path = files[0]?.path || "", baseline = choices[0]?.[0] || "";
    const [lineLabel, line] = experimentNumber("Literal line", 1, 1, 1000000);
    const [columnLabel, column] = experimentNumber("Literal column", 1, 1, 1000000);
    const [valuesLabel, values] = textInput("Candidate values (2–16)", "", "For example, 0.5, 1, 2");
    const sourceLine = element("pre", "experiment-line");
    let detectedParams = [];
    const paramWrap = element("label", "field", "Detected script parameter (optional)");
    const paramSelect = element("select");
    paramWrap.append(paramSelect);
    paramWrap.hidden = true;
    paramSelect.addEventListener("change", () => {
        const chosen = detectedParams[Number(paramSelect.value)];
        if (!chosen) return;
        line.value = chosen.line;
        column.value = chosen.column;
        showLine();
    });
    function refreshScriptParams() {
        const file = files.find(entry => entry.path === path);
        detectedParams = [];
        if (file && /\.as$/i.test(path) && window.StudioScripts?.inspectScript) {
            try {
                detectedParams = window.StudioScripts.inspectScript(file.original).constants
                    .filter(item => ["int", "uint", "float", "double"].includes(item.type));
            } catch (_) { /* Leave picker hidden if script fails to inspect. */ }
        }
        paramWrap.hidden = !detectedParams.length;
        if (detectedParams.length) {
            selectOptions(paramSelect, [
                ["", "Choose a script constant…"],
                ...detectedParams.map((item, idx) => [
                    String(idx),
                    `${item.namespace ? item.namespace + "::" : ""}${item.name} = ${item.rawLiteral}${item.unit ? " * " + item.unit : ""} (line ${item.line})`
                ])
            ], "");
        }
    }
    function showLine() {
        const file = files.find(entry => entry.path === path);
        sourceLine.textContent = file?.original.split(/\r?\n/)[Number(line.value) - 1] || "";
        sourceLine.hidden = !sourceLine.textContent;
    }
    line.addEventListener("input", showLine);
    sweepFields.append(
        element("p", "muted", state.profile
            ? `Source profile ${selectedProject()?.name || state.project} / ${state.profile}. Choose a character inside the numeric literal.`
            : "Select an editable project profile in the workspace first."),
        dropdown("Source file", files.map(file => [file.path, file.path]), path, value => {
            path = value;
            refreshScriptParams();
            showLine();
        }),
        paramWrap, lineLabel, columnLabel, sourceLine, valuesLabel,
        dropdown("Baseline opponent", choices, baseline, value => { baseline = value; }),
        element("p", "muted", "Each value plays both starting sides on the same map. Only decided pairs count toward promotion.")
    );
    refreshScriptParams();
    showLine();
    const gameField = dropdown("Game", content.games.map(value => [value, value]), setups.tournament.game, value => {
        setups[mode].game = value;
        saveTournament();
    });
    function displayMode(value) {
        mode = value;
        gameField.querySelector("select").value = setups[mode].game;
        for (const [key, input] of Object.entries(limits)) input.value = setups[mode][key];
        tournamentPicker.hidden = mode !== "tournament";
        sweepPicker.hidden = mode !== "sweep";
        candidateList.hidden = mode !== "tournament";
        sweepFields.hidden = sweepFields.disabled = mode !== "sweep";
    }
    displayMode(mode);
    row.append(
        dropdown("Experiment type", [["tournament", "Round-robin tournament"], ["sweep", "Parameter sweep"]],
            mode, displayMode),
        gameField
    );
    const budgets = element("div", "row");
    budgets.append(minutesLabel, gamesLabel, secondsLabel);
    const start = button("Start experiment", async () => {
        if (!form.reportValidity()) return;
        const maps = mode === "tournament" ? tournamentMaps : sweepMaps;
        const payload = {...state.settings, game: setups[mode].game, maps: [...maps], minutes: Number(minutes.value),
            game_limit: Number(games.value), seconds_limit: Number(seconds.value), mode};
        if (maps.size < 1 || maps.size > experimentMapLimit) {
            message(`Choose 1 to ${experimentMapLimit} installed maps.`, true);
            return;
        }
        try {
            if (mode === "tournament") {
                if (candidates.size < 2 || candidates.size > 16) throw Error("Select 2 to 16 distinct profiles.");
                payload.players = Array.from(candidates, ([choice, faction], index) =>
                    experimentPlayer(choice, index % 2, faction));
            } else {
                if (!path || dirty().length) throw Error("Select a source profile and save its changes first.");
                const source = JSON.stringify([state.project, "1", state.profile]);
                const numbers = values.value.trim().split(/[\s,]+/).filter(Boolean).map(Number);
                if (numbers.length < 2 || numbers.length > 16 || numbers.some(value => !Number.isFinite(value))) {
                    throw Error("Enter 2 to 16 comma-separated numeric candidate values.");
                }
                if (new Set(numbers).size !== numbers.length) {
                    throw Error("Enter distinct candidate values.");
                }
                payload.players = [experimentPlayer(source, 0), experimentPlayer(baseline, 1)];
                payload.sweep = {project: state.project, profile: state.profile, path,
                    line: Number(line.value), column: Number(column.value), values: numbers};
            }
            fields.disabled = true;
            state.match.snapshot = await api("/api/experiment/start", payload);
            state.experiments.selected = "";
            message("");
            updateExperimentStatus();
            pollMatch(true);
        } catch (error) {
            fields.disabled = false;
            message(errorText(error), true);
        }
    }, "button primary");
    form.addEventListener("submit", event => { event.preventDefault(); start.click(); });
    fields.append(element("h2", "", "New experiment"), row, tournamentPicker, sweepPicker,
        candidateList, sweepFields, budgets, start);
    fields.id = "experiment-form";
    return form;
}

function experimentResults(experiment, gamesOpen = true) {
    const box = element("div");
    if (!experiment) return box;
    const finalists = experiment.rankings || [];
    if (finalists.length) {
        const table = experimentTable(["Profile", "Wins", "Losses", "Draws", "Win rate", "Avg. game",
            "Script errors", "Engine errors", "Error-free", "Failures", "Timeouts"]);
        table.classList.add("experiment-standings");
        for (const row of finalists) {
            const tr = element("tr");
            const rate = typeof row.win_rate === "number" ? `${(100 * row.win_rate).toFixed(1)}%` : "—";
            const average = trackerClock(row.average_duration_seconds);
            for (const value of [row.label, row.wins, row.losses, row.draws, rate, average, row.script_errors,
                row.engine_errors, row.error_free_games, row.failures, row.timeouts]) {
                tr.append(element("td", "", String(value ?? "—")));
            }
            table.tBodies[0].append(tr);
        }
        box.append(element("h3", "", "Leaderboard"), experimentTableWrap(table));
        const winner = finalists.find(row => row.id === experiment.best_candidate_id && row.promotable);
        if (experiment.promotable && winner) box.append(button(
            `Promote ${winner.label} to a profile`, () => experimentPromotion(experiment, winner)
        ));
    }
    const games = experiment.games || [];
    if (games.length) {
        const table = experimentTable(["Game", "Map", "Matchup", "Resources", "Damage dealt", "Game time", "Script errors", "Logs"]);
        table.classList.add("experiment-game-results");
        table.tHead.rows[0].cells[3].title = "Metal produced + energy produced / 60";
        const labels = new Map(finalists.map(row => [row.id, row.label]));
        for (const game of games) {
            const tr = element("tr"), logs = element("td");
            logs.append(matchLogButton(game.run_id));
            const scriptErrors = game.unassigned_script_errors
                ? `${game.script_errors} (${game.unassigned_script_errors} unassigned)` : game.script_errors ?? 0;
            const matchup = element("td", "game-matchup");
            for (const [index, side] of ["side1", "side2"].entries()) {
                const decided = ["side1", "side2"].includes(game.outcome);
                const marker = game.outcome === "draw" ? "Draw" : decided ? (game.outcome === side ? "Winner" : "Loser") : "";
                matchup.append(matchupLine(game.side_labels?.[index] ?? labels.get(game[side]) ?? game[side], marker));
            }
            const stats = ["resources", "damage"].map(key => {
                const cell = element("td", "experiment-side-totals");
                for (const side of [0, 1]) {
                    const value = formatGameTotal(game.side_totals?.[side]?.[key]);
                    const line = element("div", "", value);
                    line.title = `Side ${side + 1}`;
                    cell.append(line);
                }
                return cell;
            });
            const time = element("td", "game-result-time", trackerClock(game.simulated_seconds));
            const phase = {timeout: "Timed out", failed: "Failed", cancelled: "Cancelled"}[game.outcome];
            if (phase) time.append(element("div", "muted", phase));
            tr.append(element("td", "", String(game.index)), element("td", "", game.map),
                matchup, ...stats, time, element("td", "", String(scriptErrors)), logs);
            table.tBodies[0].append(tr);
        }
        const details = element("details", "experiment-results-details");
        details.open = gamesOpen;
        details.append(element("summary", "", `Game results (${games.length})`), experimentTableWrap(table));
        box.append(details);
    }
    return box;
}

function experimentTable(columns) {
    const table = element("table"), header = table.createTHead().insertRow();
    for (const title of columns) header.append(element("th", "", title));
    table.createTBody();
    return table;
}

function experimentTableWrap(table) {
    const wrap = element("div", "table-wrap");
    wrap.append(table);
    return wrap;
}

function experimentPromotion(experiment, winner) {
    const box = element("div"), [label, input] = textInput("New profile name");
    const feedback = element("p", "muted");
    box.append(label, button("Create profile", async event => {
        const control = event.currentTarget;
        if (state.saving || !confirmDiscard()) return;
        control.disabled = true;
        state.saving = true; status();
        try {
            const result = await api("/api/experiment/promote", {id: experiment.id, candidate_id: winner.id, name: input.value});
            await refreshState(result.project, result.profile);
            await loadProfile();
            $("dialog").close();
            message("Winning candidate promoted to a new profile.");
        } catch (error) { feedback.textContent = errorText(error); }
        finally { state.saving = false; status(); control.disabled = false; }
    }, "button primary"), feedback);
    showDialog("Promote candidate", box);
}

function updateExperimentStatus() {
    const target = $("experiment-status");
    if (!target) return;
    $("game-parallel-control")?.updateLimit?.();
    const snapshot = state.match.snapshot;
    const running = ["running", "cancelling"].includes(snapshot?.state);
    const form = $("experiment-form");
    if (form) form.disabled = running;
    const experiment = snapshot?.operation === "experiment" && (snapshot.result?.experiment || snapshot.experiment);
    const live = target.querySelector(".live-game-tracker") || element("section", "live-game-tracker");
    const activeMatchups = experiment && running ? (snapshot.active_matchups || []) : [];
    renderLiveGames(live, activeMatchups.map(game => ({...game, limit: experiment.minutes * 60})));
    const inHistory = experiment && state.experiments.history.some(item => item.id === experiment.id);
    const signature = JSON.stringify([snapshot?.id, snapshot?.state, snapshot?.message, inHistory,
        experiment?.paused, experiment?.pause_requested, experiment?.games?.length,
        snapshot?.active_games, snapshot?.parallel_games, state.match.statusError]);
    if (target.dataset.signature === signature) return;
    target.dataset.signature = signature;
    const gamesOpen = target.querySelector(".experiment-results-details")?.open ?? true;
    target.replaceChildren();
    if (state.match.statusError) target.append(notice("Could not refresh experiment status. " + state.match.statusError, true));
    if (running && !experiment) {
        target.append(notice(snapshot.operation === "experiment" ? "Preparing experiment…" : "Another Studio operation is running."));
        if (snapshot.operation !== "experiment") return;
    }
    if (!experiment && !running) return;
    if (experiment) {
        const progress = `${experiment.completed_games} / ${experiment.scheduled_games} games`;
        const active = running ? ` · ${snapshot.active_games} active · ${snapshot.parallel_games} parallel limit` : "";
        const draining = (experiment.pause_requested || snapshot.parallel_games === 0) && snapshot.active_games > 0;
        const phase = experiment.paused ? "Paused" : snapshot.state === "running" && draining ? "Draining active games" : snapshot.state;
        const detail = experiment.paused ? " · Real-time budget still running"
            : running && snapshot.state === "running" ? "" : ` · ${snapshot.message || ""}`;
        target.append(element("h2", "", "Current experiment"),
            notice(`${phase} · ${progress}${active}${detail}`, snapshot.state === "failed"));
    }
    if (running) {
        const controls = element("div", "row");
        const paused = experiment?.paused || experiment?.pause_requested;
        const action = (label, endpoint, payload) => button(label, async event => {
            const control = event.currentTarget;
            control.disabled = true;
            try {
                state.match.snapshot = await api(endpoint, {...payload, id: snapshot.id});
                updateExperimentStatus();
            } catch (error) { message(errorText(error), true); }
            finally { control.disabled = false; }
        });
        if (experiment && snapshot.state === "running") {
            if (snapshot.parallel_games > 0) {
                controls.append(action(paused ? "Resume" : "Pause new games", "/api/experiment/pause", {paused: !paused}));
            }
        }
        if (snapshot.state === "running") controls.append(action("Cancel experiment", "/api/match/cancel", {}));
        target.append(controls);
    }
    if (experiment && (running || !inHistory)) {
        target.append(live, experimentResults(experiment, gamesOpen));
    }
}

function renderExperimentHistory() {
    const box = $("experiment-history");
    if (!box) return;
    const data = state.experiments;
    box.replaceChildren(element("h2", "", "Experiment history"));
    if (data.error) { box.append(notice(data.error, true)); return; }
    if (!data.history.length) { box.append(element("p", "muted", "No saved experiments.")); return; }
    const result = element("div");
    const draw = async () => {
        const entry = data.history.find(item => item.id === data.selected);
        result.replaceChildren(experimentResults(entry));
        if (!entry?.games?.some(game => !game.side_totals && game.run_id)) return;
        try {
            const {experiment} = await api("/api/experiment/history?" + new URLSearchParams({id: entry.id}));
            if (data.selected !== entry.id || !result.isConnected) return;
            const index = data.history.indexOf(entry);
            if (index !== -1) data.history[index] = experiment;
            const open = result.querySelector(".experiment-results-details")?.open ?? true;
            result.replaceChildren(experimentResults(experiment, open));
        } catch (error) {
            if (data.selected === entry.id && result.isConnected) {
                result.append(notice("Could not load saved game totals. " + errorText(error), true));
            }
        }
    };
    if (!data.history.some(item => item.id === data.selected)) data.selected = data.history[0].id;
    box.append(dropdown("Saved experiment", data.history.map(item => [item.id,
        `${item.mode === "sweep" ? "Sweep" : "Tournament"} · ${item.started_at} · ${item.state}`
    ]), data.selected, value => { data.selected = value; draw(); }), result);
    draw();
}

async function loadExperimentHistory(checkStatus = false) {
    const data = state.experiments;
    try {
        if (checkStatus && !matchPoll) {
            state.match.snapshot = await api("/api/match/status");
            updateExperimentStatus();
            if (["running", "cancelling"].includes(state.match.snapshot.state)) pollMatch();
        }
        data.history = (await api("/api/experiment/history")).experiments;
        data.error = "";
    } catch (error) { data.error = errorText(error); }
    renderExperimentHistory();
    updateExperimentStatus();
}

function experimentView(target) {
    const content = state.match.content;
    const capacity = element("div", "panel");
    capacity.append(gameParallelControl());
    target.append(capacity);
    if (!state.settings || !content?.games.length || !content.maps.length || !content.ais.length) {
        const panel = element("div", "panel");
        panel.append(notice("Scan installed games, maps, and AIs in Test match first."),
            button("Open Test match", () => navigate("match")));
        target.append(panel);
    } else {
        target.append(experimentForm(content));
    }
    const live = element("div", "panel"), history = element("div", "panel");
    live.id = "experiment-status";
    history.id = "experiment-history";
    target.append(live, history);
    updateExperimentStatus();
    renderExperimentHistory();
}
