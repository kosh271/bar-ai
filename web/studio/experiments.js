"use strict";

function experimentNumber(label, value, min, max) {
    const wrap = element("label", "field", label), input = element("input");
    Object.assign(input, {type: "number", value, min, max, step: "1", required: true});
    wrap.append(input);
    return [wrap, input];
}

function experimentChecks(title, choices) {
    const details = element("details");
    const grid = element("div", "series-checks");
    details.append(element("summary", "", title), grid);
    details.className = "experiment-choices";
    const selected = new Set();
    for (const [key, text, checked] of choices) {
        const label = element("label"), input = element("input");
        input.type = "checkbox";
        input.checked = !!checked;
        if (checked) selected.add(key);
        input.addEventListener("change", () => {
            if (input.checked) selected.add(key);
            else selected.delete(key);
        });
        label.append(input, document.createTextNode(text));
        grid.append(label);
    }
    return [details, selected];
}

function experimentPlayer(choice, ally) {
    const [short_name, version, profile] = JSON.parse(choice);
    return {short_name, version, profile, faction: ally === 0 ? "Armada" : "Cortex", ally};
}

function experimentForm(content) {
    const form = element("form", "panel"), fields = element("fieldset", "experiment-form");
    form.append(fields);
    const row = element("div", "row");
    let mode = "tournament", game = content.games.includes(state.match.game) ? state.match.game : content.games[0];
    const primaryMap = content.maps.find(map => map.name === state.match.map) || content.maps[0];
    const [mapList, maps] = experimentChecks("Map pool (up to 32)", content.maps.map(map => [
        map.name, map.name, map.name === primaryMap.name
    ]));
    const choices = content.ais.flatMap(ai => (ai.profiles.length ? ai.profiles : [{key: "", name: "Default"}]).map(
        profile => [JSON.stringify([ai.short_name, ai.version, profile.key]), `${ai.name} · ${profile.name}`]
    ));
    const [candidateList, candidates] = experimentChecks("Tournament profiles (2–16)", choices);
    candidateList.open = true;
    const [minutesLabel, minutes] = experimentNumber("Game time limit (minutes)", Math.min(state.match.minutes, 120), 1, 1193046);
    const [gamesLabel, games] = experimentNumber("Game limit", 24, 1, 100);
    const [secondsLabel, seconds] = experimentNumber("Real-time limit (seconds)", 1200, 1, 21600);
    const sweepFields = element("fieldset", "experiment-form");
    const files = state.files.filter(file => /\.(?:as|json)$/i.test(file.path));
    let path = files[0]?.path || "", baseline = choices[0]?.[0] || "";
    const [lineLabel, line] = experimentNumber("Literal line", 1, 1, 1000000);
    const [columnLabel, column] = experimentNumber("Literal column", 1, 1, 1000000);
    const [valuesLabel, values] = textInput("Candidate values (2–16)", "", "For example, 0.5, 1, 2");
    const sourceLine = element("pre", "experiment-line");
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
        dropdown("Source file", files.map(file => [file.path, file.path]), path, value => { path = value; showLine(); }),
        lineLabel, columnLabel, sourceLine, valuesLabel,
        dropdown("Baseline opponent", choices, baseline, value => { baseline = value; }),
        element("p", "muted", "Each value plays both starting sides on the same map. Only decided pairs count toward promotion.")
    );
    showLine();
    function displayMode(value) {
        mode = value;
        candidateList.hidden = mode !== "tournament";
        sweepFields.hidden = sweepFields.disabled = mode !== "sweep";
    }
    displayMode(mode);
    row.append(
        dropdown("Experiment type", [["tournament", "Round-robin tournament"], ["sweep", "Parameter sweep"]],
            mode, displayMode),
        dropdown("Game", content.games.map(value => [value, value]), game, value => { game = value; })
    );
    const budgets = element("div", "row");
    budgets.append(minutesLabel, gamesLabel, secondsLabel);
    const start = button("Start experiment", async () => {
        if (!form.reportValidity()) return;
        const payload = {...state.settings, game, maps: [...maps], minutes: Number(minutes.value),
            game_limit: Number(games.value), seconds_limit: Number(seconds.value), mode};
        if (maps.size < 1 || maps.size > 32) {
            message("Choose 1 to 32 installed maps.", true);
            return;
        }
        try {
            if (mode === "tournament") {
                if (candidates.size < 2 || candidates.size > 16) throw Error("Select 2 to 16 distinct profiles.");
                payload.players = [...candidates].map((choice, index) => experimentPlayer(choice, index % 2));
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
    fields.append(element("h2", "", "New experiment"), row, mapList, candidateList, sweepFields, budgets, start);
    fields.id = "experiment-form";
    return form;
}

function experimentResults(experiment) {
    const box = element("div");
    if (!experiment) return box;
    const finalists = experiment.rankings || [];
    if (finalists.length) {
        const table = experimentTable(["Profile", "Wins", "Losses", "Draws", "Win rate", "Avg. game",
            "Script errors", "Engine errors", "Error-free", "Failures", "Timeouts"]);
        for (const row of finalists) {
            const tr = element("tr");
            const rate = typeof row.win_rate === "number" ? `${(100 * row.win_rate).toFixed(1)}%` : "—";
            const average = typeof row.average_duration_seconds === "number"
                ? `${row.average_duration_seconds.toFixed(1)}s` : "—";
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
        const table = experimentTable(["Game", "Map", "Matchup", "Outcome", "Game time", "Script errors", "Logs"]);
        const labels = new Map(finalists.map(row => [row.id, row.label]));
        for (const game of games) {
            const tr = element("tr"), logs = element("td");
            logs.append(matchLogButton(game.run_id));
            const scriptErrors = game.unassigned_script_errors
                ? `${game.script_errors} (${game.unassigned_script_errors} unassigned)` : game.script_errors ?? 0;
            for (const value of [game.index, game.map,
                `${labels.get(game.side1) ?? game.side1} vs ${labels.get(game.side2) ?? game.side2}`,
                game.outcome, typeof game.simulated_seconds === "number" ? `${game.simulated_seconds}s` : "—", scriptErrors]) {
                tr.append(element("td", "", String(value)));
            }
            tr.append(logs);
            table.tBodies[0].append(tr);
        }
        const details = element("details");
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
    const snapshot = state.match.snapshot;
    const running = ["running", "cancelling"].includes(snapshot?.state);
    const form = $("experiment-form");
    if (form) form.disabled = running;
    const experiment = snapshot?.operation === "experiment" && (snapshot.result?.experiment || snapshot.experiment);
    const inHistory = experiment && state.experiments.history.some(item => item.id === experiment.id);
    const signature = JSON.stringify([snapshot?.id, snapshot?.state, snapshot?.message, inHistory,
        experiment?.paused, experiment?.pause_requested, experiment?.games?.length, state.match.statusError]);
    if (target.dataset.signature === signature) return;
    target.dataset.signature = signature;
    target.replaceChildren();
    if (state.match.statusError) target.append(notice("Could not refresh experiment status. " + state.match.statusError, true));
    if (running && !experiment) {
        target.append(notice(snapshot.operation === "experiment" ? "Preparing experiment…" : "Another Studio operation is running."));
        if (snapshot.operation !== "experiment") return;
    }
    if (!experiment && !running) return;
    if (experiment) {
        const progress = `${experiment.completed_games} / ${experiment.scheduled_games} games`;
        target.append(element("h2", "", "Current experiment"),
            notice(`${experiment.paused ? "Paused" : snapshot.state} · ${progress} · ${snapshot.message || ""}`, snapshot.state === "failed"));
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
            controls.append(action(paused ? "Resume" : "Pause after current game", "/api/experiment/pause", {paused: !paused}));
        }
        if (snapshot.state === "running") controls.append(action("Cancel experiment", "/api/match/cancel", {}));
        target.append(controls);
    }
    if (experiment && (running || !inHistory)) target.append(experimentResults(experiment));
}

function renderExperimentHistory() {
    const box = $("experiment-history");
    if (!box) return;
    const data = state.experiments;
    box.replaceChildren(element("h2", "", "Experiment history"));
    if (data.error) { box.append(notice(data.error, true)); return; }
    if (!data.history.length) { box.append(element("p", "muted", "No saved experiments.")); return; }
    const result = element("div");
    const draw = () => result.replaceChildren(experimentResults(data.history.find(item => item.id === data.selected)));
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
