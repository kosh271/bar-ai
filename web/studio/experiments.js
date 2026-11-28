"use strict";

function preferredExperimentGame(games) {
    const byVersion = new Intl.Collator("en", {numeric: true});
    const versioned = games.filter(game => !game.includes("$VERSION"));
    const available = versioned.length ? versioned : games;
    // Rapid publishes official BAR builds with an increasing test-<number> revision.
    const official = available.filter(game => /^Beyond All Reason test-\d+(?:-|$)/i.test(game));
    return (official.length ? official : available).reduce(
        (latest, game) => byVersion.compare(game, latest) > 0 ? game : latest, "",
    );
}

function experimentGameDraft(games, saved, testMatchGame) {
    const priorDefault = games.includes(testMatchGame) ? testMatchGame : games[0];
    const selected = games.includes(saved?.game) &&
        (saved.game_selected ?? (!saved.game.includes("$VERSION") && saved.game !== priorDefault));
    return {game: selected ? saved.game : preferredExperimentGame(games), game_selected: selected};
}

function experimentNumber(label, value, min, max) {
    const wrap = element("label", "field", label), input = element("input");
    Object.assign(input, {type: "number", value, min, max, step: "1", required: true});
    wrap.append(describeControl(input, StudioTips.field(label)));
    return [wrap, input];
}

function tournamentProfiles(choices, savedPlayers, changed) {
    const details = element("details", "experiment-choices");
    details.open = true;
    const options = new Map(choices);
    const selected = new Map(savedPlayers.map(({ai, faction}) => [JSON.stringify(ai), faction])
        .filter(([key]) => options.has(key)));
    const summary = describeControl(element("summary"), "Show the installed profiles available for this tournament. Each added profile introduces more matchups.");
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
        describeControl(input, "Enter this installed profile in the tournament. Adding a profile increases the number of pairings competing for the game budget.");
        checks.push(input);
        const faction = dropdown("Faction", tournamentFactionOptions, selected.get(key) || "Armada", value => {
            selected.set(key, value);
            changed();
        }, "Pick a fixed faction, cycle through factions with Rotate, or select Random. Both games in a mirrored pair use the same factions.");
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
        "Select 2–16 profiles. Rotate cycles through Armada, Cortex, and Legion. Random draws a faction for each mirrored pair and keeps it for both starting sides."));
    return [details, selected];
}

const experimentFactions = ["Armada", "Cortex", "Legion"];
const tournamentFactionOptions = [
    ...experimentFactions.map(faction => [faction, `${faction} (fixed)`]),
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
    const byName = new Map(maps.map(map => [map.name, map]));
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
        describeControl(control, action === "Add"
            ? "Include this map in the experiment. Map terrain can change which strategies do well."
            : "Take this map out of the experiment pool. It stays installed and can be added again.");
        control.disabled = disabled;
        const label = element("span", "", name);
        const dimensions = mapSizeComparison(byName.get(name));
        if (dimensions) {
            label.classList.add("distance-value");
            describeControl(label, dimensions);
            StudioTips.dynamic(control, () => dimensions);
        }
        row.append(label, control);
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
    const drafts = state.experiments;
    const form = element("form", "panel experiment-setup"), fields = element("fieldset", "experiment-form");
    form.append(fields);
    const row = element("div", "row");
    const saved = drafts.tournament;
    const defaults = {minutes: Math.min(state.match.minutes, 120), game_limit: 24,
        seconds_limit: 1200, start_options: QUICKSTART_DEFAULTS, seed: null};
    const setups = Object.fromEntries(["tournament", "sweep"].map(mode => [
        mode, {...defaults, ...drafts[mode], ...experimentGameDraft(content.games, drafts[mode], state.match.game)},
    ]));
    let mode = drafts.mode;
    const tournament = tournamentMapPool(content.maps, state.match.map, saved?.maps);
    const [tournamentPicker, tournamentMaps] = experimentMapPicker(tournament.available, tournament.selected, saveDraft);
    const primaryMap = content.maps.find(map => map.name === state.match.map) || content.maps[0];
    const installedMaps = new Set(content.maps.map(map => map.name));
    const sweepSelection = drafts.sweep?.maps?.filter(map => installedMaps.has(map)) ?? [primaryMap.name];
    const [sweepPicker, sweepMaps] = experimentMapPicker(content.maps, sweepSelection, saveDraft);
    const choices = content.ais.flatMap(ai => (ai.profiles.length ? ai.profiles : [{key: "", name: "Default"}]).map(
        profile => [JSON.stringify([ai.short_name, ai.version, profile.key]), `${ai.name} · ${profile.name}`]
    ));
    const [candidateList, candidates] = tournamentProfiles(choices, saved?.players || [], saveDraft);
    const [minutesLabel, minutes] = experimentNumber("Game time limit (minutes)", setups.tournament.minutes, 1, 1193046);
    const [gamesLabel, games] = experimentNumber("Game limit", setups.tournament.game_limit, 1, 100);
    const [elapsedLabel, elapsed] = experimentNumber("Real-time limit (minutes)", 20, 1 / 60, 360);
    elapsed.step = "any";
    const limits = {minutes, game_limit: games, seconds_limit: elapsed};
    const [seedLabel, seed] = experimentNumber("Schedule seed (optional)", "", 0, 4294967295);
    describeControl(seed, "Leave blank for a generated seed. To repeat the schedule, use the saved seed with the same maps and profiles in the same order.");
    seed.required = false;
    const coverage = element("p", "muted experiment-budget");
    coverage.setAttribute("role", "status");
    const starting = startingConditions(setups[mode].start_options, options => {
        setups[mode].start_options = options;
        saveDraft();
    });
    function updateCoverage() {
        const needed = tournamentMaps.size * candidates.size * (candidates.size - 1);
        coverage.hidden = mode !== "tournament" || !needed || !games.validity.valid;
        if (coverage.hidden) return;
        const limit = games.valueAsNumber;
        const scheduled = Math.min(needed, Math.max(1, limit - limit % 2));
        coverage.classList.toggle("inline-error", scheduled < needed);
        coverage.textContent = scheduled < needed
            ? `Full coverage needs ${needed} games for ${candidates.size} profiles across ${tournamentMaps.size} selected maps. This limit schedules ${scheduled} and leaves ${needed - scheduled} unscheduled.`
            : `Full coverage fits in ${needed} games, with both starting sides for every pairing on each selected map.`;
    }
    function saveDraft(persist = true) {
        const {game, game_selected, minutes, game_limit, seconds_limit, start_options, seed} = setups[mode];
        const draft = {game, game_selected, minutes, game_limit, seconds_limit, start_options, seed};
        if (mode === "tournament") {
            draft.maps = [...tournamentMaps];
            draft.players = Array.from(candidates, ([key, faction]) => ({ai: JSON.parse(key), faction}));
        } else {
            Object.assign(draft, {maps: [...sweepMaps], baseline: JSON.parse(baseline), values: values.value});
        }
        drafts[mode] = draft;
        if (persist) rememberPreference({[mode]: draft});
        updateCoverage();
    }
    for (const [key, input] of Object.entries(limits)) {
        input.addEventListener("input", () => {
            if (!input.validity.valid) { updateCoverage(); return; }
            const value = key === "seconds_limit" ? Math.round(input.valueAsNumber * 60) : input.valueAsNumber;
            if (setups[mode][key] === value) { updateCoverage(); return; }
            setups[mode][key] = value;
            saveDraft();
        });
    }
    seed.addEventListener("input", () => {
        if (!seed.validity.valid) return;
        setups[mode].seed = seed.value === "" ? null : seed.valueAsNumber;
        saveDraft();
    });
    const sweepFields = element("fieldset", "experiment-form");
    const files = state.files.filter(file => /\.(?:as|json)$/i.test(file.path));
    const sourceKey = JSON.stringify([state.project, state.profile]);
    const previousSource = drafts.sweepSources.get(sourceKey);
    const savedSource = files.some(file => file.path === previousSource?.path) ? previousSource : null;
    let path = savedSource?.path ?? files[0]?.path ?? "";
    const savedBaseline = JSON.stringify(setups.sweep.baseline);
    let baseline = choices.some(([key]) => key === savedBaseline) ? savedBaseline : choices[0]?.[0] || "";
    const [lineLabel, line] = experimentNumber("Literal line", savedSource?.line ?? 1, 1, 1000000);
    const [columnLabel, column] = experimentNumber("Literal column", savedSource?.column ?? 1, 1, 1000000);
    const [valuesLabel, values] = textInput("Candidate values (2–16)", setups.sweep.values ?? "", "For example, 0.5, 1, 2");
    values.maxLength = 512;
    values.addEventListener("input", () => saveDraft(false));
    values.addEventListener("change", () => saveDraft());
    const sourceLine = element("pre", "experiment-line");
    const literalFeedback = element("p", "muted experiment-literal-feedback");
    literalFeedback.setAttribute("role", "status");
    const literalConfigs = new WeakMap();
    let previewTimer, previewController;
    let detectedParams = [];
    const paramWrap = element("label", "field", "Detected script parameter (optional)");
    const paramSelect = element("select");
    paramWrap.append(describeControl(paramSelect, "Pick a number Studio found in the script. It fills in the line and column so you can identify the number without counting characters."));
    paramWrap.hidden = true;
    paramSelect.addEventListener("change", () => {
        if (paramSelect.value === "") return;
        const chosen = detectedParams[Number(paramSelect.value)];
        if (!chosen) return;
        line.value = chosen.line;
        column.value = chosen.column;
        rememberSource();
        showLine();
    });
    function rememberSource() {
        if (path && line.validity.valid && column.validity.valid) {
            drafts.sweepSources.set(sourceKey, {path, line: line.valueAsNumber, column: column.valueAsNumber});
        }
    }
    function refreshScriptParams() {
        const file = files.find(entry => entry.path === path);
        detectedParams = [];
        if (file && /\.as$/i.test(path) && window.StudioScripts?.inspectScript) {
            try {
                detectedParams = window.StudioScripts.inspectScript(file.original).constants
                    .filter(item => ["int", "uint", "float", "double"].includes(item.type))
                    .map(item => {
                        const prefix = file.original.slice(0, item.start).replace(/^\ufeff/, "").split(/\r\n?|\n/).at(-1);
                        return {...item, column: Array.from(prefix).length + 1};
                    });
            } catch (_) { /* Leave picker hidden if script fails to inspect. */ }
        }
        paramWrap.hidden = !detectedParams.length;
        if (detectedParams.length) {
            const selected = detectedParams.findIndex(item => item.line === line.valueAsNumber && item.column === column.valueAsNumber);
            selectOptions(paramSelect, [
                ["", "Choose a script constant…"],
                ...detectedParams.map((item, idx) => [
                    String(idx),
                    `${item.namespace ? item.namespace + "::" : ""}${item.name} = ${item.rawLiteral}${item.unit ? " * " + item.unit : ""} (line ${item.line}, column ${item.column})`
                ])
            ], selected < 0 ? "" : String(selected));
        }
    }
    function literalSelection() {
        const file = files.find(entry => entry.path === path);
        return {project: state.project, profile: state.profile, path,
            line: line.valueAsNumber, column: column.valueAsNumber, sha256: file?.sha256};
    }
    function literalDistance(file, lineNumber, columnNumber, chars) {
        if (/\.as$/i.test(file.path)) {
            const constant = detectedParams.find(item => item.line === lineNumber && item.column === columnNumber);
            return /^elmos?$/i.test(constant?.unit || constant?.meta?.unit || "") ? constant.value : NaN;
        }
        try {
            const prefix = file.original.split(/(\r\n?|\n)/).slice(0, 2 * (lineNumber - 1)).join("");
            const bom = lineNumber === 1 && file.original.startsWith("\ufeff") ? 1 : 0;
            const offset = prefix.length + bom + chars.slice(0, columnNumber - 1).join("").length;
            if (!literalConfigs.has(file)) literalConfigs.set(file, C.parse(file.original));
            let node = literalConfigs.get(file);
            while (node?.children) {
                node = node.children.find(({node}) => node.start <= offset && offset < node.end)?.node;
            }
            const factor = node?.kind === "number" && distanceFactor(node.path);
            return factor ? node.value * factor : NaN;
        } catch (_) {
            return NaN;
        }
    }
    function showLine() {
        clearTimeout(previewTimer);
        previewController?.abort();
        const file = files.find(entry => entry.path === path);
        const text = file?.original.replace(/^\ufeff/, "").split(/\r\n?|\n/)[line.valueAsNumber - 1];
        sourceLine.textContent = text ?? "";
        sourceLine.hidden = text === undefined;
        literalFeedback.textContent = "";
        literalFeedback.classList.remove("inline-error");
        if (mode !== "sweep" || !file || !line.validity.valid || !column.validity.valid) return;
        const selection = literalSelection();
        const chars = Array.from(text ?? "");
        const selected = selection.column - 1;
        const cursor = element("span", "literal-column", chars[selected]);
        if (selected < chars.length) sourceLine.replaceChildren(chars.slice(0, selected).join(""), cursor, chars.slice(selected + 1).join(""));
        const controller = new AbortController();
        previewController = controller;
        previewTimer = setTimeout(async () => {
            if (!form.isConnected || controller.signal.aborted || fields.disabled) return;
            try {
                const {column, length} = await api("/api/experiment/literal", selection, controller.signal);
                if (controller.signal.aborted || !form.isConnected) return;
                const start = column - 1, end = start + length;
                const token = element("mark", "literal-token");
                const elmos = literalDistance(file, selection.line, column, chars);
                if (Number.isFinite(elmos)) {
                    token.classList.add("distance-value");
                    token.tabIndex = 0;
                    StudioTips.dynamic(token, () => StudioTips.compareDistance(elmos));
                }
                token.append(chars.slice(start, selected).join(""), cursor, chars.slice(selected + 1, end).join(""));
                sourceLine.replaceChildren(chars.slice(0, start).join(""), token, chars.slice(end).join(""));
                literalFeedback.textContent = `Each candidate replaces the full number ${chars.slice(start, end).join("")}.`;
            } catch (error) {
                if (controller.signal.aborted || !form.isConnected) return;
                literalFeedback.classList.add("inline-error");
                literalFeedback.textContent = errorText(error);
            }
        }, 140);
    }
    for (const control of [line, column]) control.addEventListener("input", () => {
        paramSelect.value = "";
        rememberSource();
        showLine();
    });
    sweepFields.append(
        element("p", "muted", state.profile
            ? `Source profile ${selectedProject()?.name || state.project} / ${state.profile}. Choose a character inside the numeric literal.`
            : "Select an editable project profile in the workspace first."),
        dropdown("Source file", files.map(file => [file.path, file.path]), path, value => {
            path = value;
            line.value = column.value = 1;
            rememberSource();
            refreshScriptParams();
            showLine();
        }),
        paramWrap, lineLabel, columnLabel, sourceLine, literalFeedback, valuesLabel,
        dropdown("Baseline opponent", choices, baseline, value => { baseline = value; saveDraft(); }),
        element("p", "muted", "Each value plays both starting sides on the same map. Promotion needs at least two clean, decided pairs and a paired score above 50%. Draws count as half a point.")
    );
    refreshScriptParams();
    const gameField = dropdown("Game", content.games.map(value => [
        value, value.includes("$VERSION") ? value.replace("$VERSION", "(local development archive)") : value,
    ]), setups[mode].game, value => {
        setups[mode].game = value;
        setups[mode].game_selected = true;
        saveDraft();
    });
    function displayMode(value) {
        drafts.mode = mode = value;
        gameField.querySelector("select").value = setups[mode].game;
        for (const [key, input] of Object.entries(limits)) input.value = setups[mode][key];
        elapsed.value = Number((setups[mode].seconds_limit / 60).toFixed(6));
        seed.value = setups[mode].seed ?? "";
        tournamentPicker.hidden = mode !== "tournament";
        sweepPicker.hidden = mode !== "sweep";
        candidateList.hidden = mode !== "tournament";
        starting.reset(setups[mode].start_options);
        sweepFields.hidden = sweepFields.disabled = mode !== "sweep";
        updateCoverage();
        showLine();
    }
    displayMode(mode);
    row.append(
        dropdown("Experiment type", [["tournament", "Round-robin tournament"], ["sweep", "Parameter sweep"]],
            mode, displayMode),
        gameField
    );
    const budgets = element("div", "row");
    budgets.append(minutesLabel, gamesLabel, elapsedLabel, seedLabel);
    const feedback = element("p", "inline-error");
    feedback.setAttribute("role", "status");
    const start = button("Start experiment", async () => {
        if (form.dataset.pending === "true") return;
        feedback.textContent = "";
        if (!form.reportValidity()) {
            const invalid = form.querySelector("input:invalid, select:invalid");
            const label = invalid?.labels?.[0]?.firstChild?.textContent;
            feedback.textContent = label && invalid.validationMessage
                ? `${label}. ${invalid.validationMessage}` : "Correct the highlighted field and try again.";
            return;
        }
        const maps = mode === "tournament" ? tournamentMaps : sweepMaps;
        const payload = {...state.settings, game: setups[mode].game, maps: [...maps], minutes: Number(minutes.value),
            game_limit: Number(games.value), seconds_limit: Math.round(elapsed.valueAsNumber * 60), mode};
        if (maps.size < 1 || maps.size > experimentMapLimit) {
            feedback.textContent = `Choose 1 to ${experimentMapLimit} installed maps.`;
            return;
        }
        payload.start_options = starting.read();
        if (seed.value !== "") payload.seed = seed.valueAsNumber;
        try {
            if (mode === "tournament") {
                if (candidates.size < 2 || candidates.size > 16) throw Error("Select 2 to 16 distinct profiles.");
                payload.players = Array.from(candidates, ([choice, faction], index) =>
                    experimentPlayer(choice, index % 2, faction));
            } else {
                if (!state.profile || !path) throw Error("Select an editable workspace profile with a source file.");
                if (dirty().length) throw Error("Save the workspace profile's changes before starting a sweep.");
                const source = JSON.stringify([state.project, "1", state.profile]);
                const numbers = values.value.trim().split(/[\s,]+/).filter(Boolean).map(Number);
                if (numbers.length < 2 || numbers.length > 16 || numbers.some(value => !Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER)) {
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
            form.dataset.pending = "true";
            if (mode === "sweep") {
                const selection = literalSelection();
                await api("/api/experiment/literal", selection);
                payload.sweep.sha256 = selection.sha256;
            }
            state.match.snapshot = await api("/api/experiment/start", payload);
            if (state.match.snapshot.state === "completed") {
                drafts.selected = state.match.snapshot.id;
            }
            delete form.dataset.pending;
            message("");
            updateExperimentStatus();
            $("experiment-status")?.parts.progress.focus({preventScroll: true});
            pollMatch(true);
        } catch (error) {
            delete form.dataset.pending;
            fields.disabled = false;
            feedback.textContent = "Could not start the experiment. " + errorText(error);
            start.focus({preventScroll: true});
        }
    }, "button primary");
    form.addEventListener("submit", event => { event.preventDefault(); start.click(); });
    fields.append(element("h2", "", "New experiment"), row, tournamentPicker, sweepPicker,
        candidateList, starting.fields, sweepFields, budgets, coverage, start, feedback);
    fields.id = "experiment-form";
    return form;
}

function experimentComparisons(experiment) {
    const [ai, faction, pairing] = ["AI vs. AI", "Faction vs. faction", "AI + faction vs. AI + faction"]
        .map(title => ({title, headings: new Map(), records: new Map()}));
    const sides = ["side1", "side2"];
    const schedule = new Map();
    const players = new Map();
    for (const planned of experiment.schedule || []) {
        schedule.set(planned.index, planned.players);
        for (const player of planned.players) {
            players.set(planned[sides[player.ally]], player);
        }
    }
    const rankLabels = new Map((experiment.rankings || []).map(row => [row.id, row.label]));
    const labelFaction = label => label?.includes(" · ") ? label.split(" · ").at(-1) : undefined;
    const profiles = new Map();
    const profileKeys = new Map();
    function registerProfile(id, label = id) {
        if (profileKeys.has(id)) return;
        const player = players.get(id);
        const key = player?.short_name
            ? JSON.stringify([player.short_name, player.version, player.profile]) : id;
        profileKeys.set(id, key);
        if (profiles.has(key)) return;
        const parts = label.split(" · ");
        if (parts.length > 1 && tournamentFactionOptions.some(([value]) => value === parts.at(-1))) parts.pop();
        const text = parts.join(" · ");
        const sweepVariant = experiment.mode === "sweep" && id !== "baseline";
        const profile = sweepVariant ? text : parts.length > 1 ? parts.pop() : "";
        const name = sweepVariant ? experiment.sweep?.project || "Sweep candidates" : parts.join(" · ");
        const group = player?.short_name ? JSON.stringify([player.short_name, player.version]) : name;
        const identity = player?.short_name ? `${player.short_name} / ${player.version}` : id;
        profiles.set(key, {group, name, profile, identity});
    }
    const games = (experiment.games || []).filter(game =>
        game.outcome !== "cancelled" && sides.every(side => game[side]));
    for (const [id, label] of rankLabels) registerProfile(id, label);
    for (const game of games) {
        for (const [index, side] of sides.entries()) {
            registerProfile(game[side], game.side_labels?.[index]);
        }
    }
    const profileCounts = new Map();
    const groupsByName = new Map();
    for (const {profile, name, group} of profiles.values()) {
        const pair = JSON.stringify([group, profile]);
        profileCounts.set(pair, (profileCounts.get(pair) || 0) + 1);
        if (!groupsByName.has(name)) groupsByName.set(name, new Set());
        groupsByName.get(name).add(group);
    }
    for (const [key, {name, group, profile, identity}] of profiles) {
        const distinguishAI = groupsByName.get(name).size > 1;
        const distinguishProfile = profileCounts.get(JSON.stringify([group, profile])) > 1;
        ai.headings.set(key, [
            {key: group, label: distinguishAI ? `${name} (${identity})` : name},
            {key, label: distinguishProfile ? `${profile} (${identity})` : profile},
        ]);
    }

    function record(matrix, keys, outcome) {
        if (keys.includes(undefined) || (outcome !== "draw" && !sides.includes(outcome))) return;
        for (const [index, row] of keys.entries()) {
            const column = keys[1 - index];
            const key = JSON.stringify([row, column]);
            const stats = matrix.records.get(key) || {wins: 0, losses: 0, draws: 0, games: 0};
            stats.games++;
            matrix.records.set(key, stats);
            if (row === column) break;
            const result = outcome === "draw" ? "draws" : outcome === sides[index] ? "wins" : "losses";
            stats[result]++;
        }
    }
    for (const game of games) {
        const planned = schedule.get(game.index);
        const matchProfiles = sides.map(side => profileKeys.get(game[side]));
        const factions = sides.map((side, index) => [
            game.factions?.[index], planned?.find(player => player.ally === index)?.faction,
            labelFaction(game.side_labels?.[index]), labelFaction(rankLabels.get(game[side])),
        ].find(value => experimentFactions.includes(value)));
        const pairings = factions.map((name, index) => {
            if (!name) return undefined;
            faction.headings.set(name, [{key: name, label: name, faction: name}]);
            const profile = matchProfiles[index];
            const key = JSON.stringify([profile, name]);
            pairing.headings.set(key, [...ai.headings.get(profile), {key, label: name, faction: name}]);
            return key;
        });
        record(ai, matchProfiles, game.outcome);
        record(faction, factions, game.outcome);
        record(pairing, pairings, game.outcome);
    }
    return [ai, faction, pairing];
}

let experimentMatrixId = 0;

function experimentMatrixSpans(entries, prefix) {
    const levels = entries[0].path.map(() => []);
    const paths = entries.map(({path}, index) => path.map((heading, level) => {
        const spans = levels[level];
        const prior = spans.at(-1);
        if (prior?.key === heading.key) {
            prior.span++;
            return prior;
        }
        const span = {...heading, index, span: 1, id: `${prefix}-${level}-${index}`};
        spans.push(span);
        return span;
    }));
    return {levels, paths};
}

function experimentMatrixTable(records, rows, columns, prefix) {
    const table = element("table", "experiment-matrix");
    table.setAttribute("aria-labelledby", `${prefix}-title`);
    const depth = rows[0].path.length;
    const rowCols = element("colgroup", "matrix-row-labels");
    rowCols.span = depth;
    table.append(rowCols);

    const left = experimentMatrixSpans(rows, `${prefix}-row`);
    const top = experimentMatrixSpans(columns, `${prefix}-col`);
    function header(heading, level, axis) {
        const cell = element("th", `matrix-heading-level-${level + 1}`, heading.label);
        cell.id = heading.id;
        if (heading.faction) cell.dataset.faction = heading.faction.toLowerCase();
        if (axis === "col") cell.colSpan = heading.span;
        else cell.rowSpan = heading.span;
        if (level === depth - 1) cell.scope = axis;
        return cell;
    }

    const head = table.createTHead();
    for (const [level, spans] of top.levels.entries()) {
        const tr = head.insertRow();
        if (level === 0) {
            const corner = element("td", "matrix-corner");
            corner.colSpan = corner.rowSpan = depth;
            corner.append(element("span", "matrix-axis-opponent", "Opponent →"),
                element("span", "matrix-axis-side", "Win rate for ↓"));
            tr.append(corner);
        }
        for (const span of spans) tr.append(header(span, level, "col"));
    }
    const body = table.createTBody();
    for (const [rowIndex, row] of rows.entries()) {
        const tr = body.insertRow();
        const rowPath = left.paths[rowIndex];
        for (const [level, span] of rowPath.entries()) {
            if (span.index === rowIndex) tr.append(header(span, level, "row"));
        }
        for (const [columnIndex, column] of columns.entries()) {
            const stats = records.get(JSON.stringify([row.key, column.key]));
            const cell = tr.insertCell();
            cell.headers = [...rowPath, ...top.paths[columnIndex]].map(span => span.id).join(" ");
            if (!stats) {
                cell.className = "muted";
                cell.textContent = "—";
            } else if (row.key === column.key) {
                cell.className = "muted";
                cell.textContent = `${stats.games} mirror ${stats.games === 1 ? "game" : "games"}`;
            } else {
                const {wins, losses, draws, games} = stats;
                if (wins !== losses) cell.classList.add(wins > losses ? "more-wins" : "more-losses");
                cell.append(element("strong", "", `${(100 * wins / games).toFixed(1)}%`),
                    element("div", "muted", `${wins}–${losses}–${draws}`));
            }
        }
    }
    return table;
}

function experimentMatrixView({title, headings, records}, firstOpponent) {
    const view = element("div", "experiment-matrix-view");
    view.dataset.matrix = title;
    const rows = [...headings].map(([key, path]) => ({key, path})).sort((a, b) => {
        for (const [index, heading] of a.path.entries()) {
            const other = b.path[index];
            const order = heading.label.localeCompare(other.label) || heading.key.localeCompare(other.key);
            if (order) return order;
        }
        return 0;
    });
    const depth = rows[0].path.length;
    const prefix = `experiment-matrix-${++experimentMatrixId}`;
    const top = element("div", "experiment-matrix-top");
    const heading = element("h4", "", title);
    heading.id = `${prefix}-title`;
    const control = element("label", "matrix-opponent-columns", "Opponent columns");
    const select = element("select");
    select.setAttribute("aria-label", `Opponent columns for ${title}`);
    control.hidden = true;
    control.append(select);
    const wrap = element("div", "experiment-matrix-scroll");
    wrap.tabIndex = 0;
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", title);
    top.append(heading, control);
    view.append(top, wrap);

    let capacity = 0;
    let pages = [];
    function showPage(page) {
        const columns = pages[page];
        view.dataset.firstOpponent = columns[0].key;
        wrap.replaceChildren(experimentMatrixTable(records, rows, columns, prefix));
        select.value = String(page);
    }
    select.addEventListener("change", () => showPage(Number(select.value)));
    const resize = new ResizeObserver(() => {
        if (!view.isConnected) {
            resize.disconnect();
            return;
        }
        const width = wrap.clientWidth;
        if (!width) return;
        const rowLabelWidth = Math.min(76, Math.floor((width - 32) / depth),
            Math.max(48, Math.floor(width / (2 * depth))));
        wrap.style.setProperty("--matrix-row-label-width", `${rowLabelWidth}px`);
        const fit = Math.max(1, Math.floor((width - depth * rowLabelWidth) / 50));
        if (fit === capacity) return;
        capacity = fit;
        const anchor = view.dataset.firstOpponent || firstOpponent;
        pages = [];
        for (let start = 0; start < rows.length;) {
            const limit = Math.min(start + fit, rows.length);
            let end = limit;
            // Keep a profile's factions together when the page can fit them.
            if (depth === 3 && fit >= experimentFactions.length) {
                while (end < rows.length && end > start && rows[end].path[1].key === rows[end - 1].path[1].key) end--;
            }
            if (end === start) end = limit;
            pages.push(rows.slice(start, end));
            start = end;
        }
        let first = 1;
        select.replaceChildren(...pages.map((columns, index) => {
            const last = first + columns.length - 1;
            const range = first === last ? first : `${first}–${last}`;
            const label = columns[0].path.map(heading => heading.label).filter(Boolean).join(" · ");
            const option = element("option", "", `${range} of ${rows.length} · ${label}`);
            option.value = String(index);
            first = last + 1;
            return option;
        }));
        control.hidden = pages.length === 1;
        showPage(Math.max(0, pages.findIndex(columns => columns.some(column => column.key === anchor))));
    });
    requestAnimationFrame(() => {
        if (view.isConnected) resize.observe(wrap);
    });
    return view;
}

function experimentMatchupMatrices(experiment, openSections, opponentColumns) {
    const details = element("details", "experiment-matchups");
    details.dataset.section = "matchups";
    details.open = openSections?.has("matchups") ?? true;
    const legend = element("p", "muted");
    legend.append(element("strong", "", "Each cell shows the left side’s win rate against the opponent at the top."),
        " The record underneath is wins–losses–draws for the left side. Green means the left side won more games, and red means the opponent did. A dash means no scored games. Timeouts, failures, and cancellations are excluded.");
    details.append(element("summary", "", "Matchup matrices"), legend);
    for (const [index, matrix] of experimentComparisons(experiment).entries()) {
        if (matrix.headings.size) {
            details.append(experimentMatrixView(matrix, opponentColumns?.get(matrix.title)));
        } else {
            details.append(element("h4", "", matrix.title), element("p", "muted",
                index === 0 ? "No recorded AI profiles." : "No recorded factions."));
        }
    }
    return details;
}

function experimentResults(experiment, openSections = null, opponentColumns = null) {
    const box = element("div");
    if (!experiment) return box;
    if (experiment.start_options) box.append(element("p", "muted", startingConditionsText(experiment.start_options)));
    if (experiment.seed !== undefined) box.append(element("p", "muted", `Schedule seed ${experiment.seed}`));
    const coverage = experiment.coverage;
    if (coverage) box.append(element("p", "muted",
        `${coverage.completed_pairs} / ${coverage.scheduled_pairs} mirrored pairs completed · ` +
        `${coverage.scheduled_pairs} / ${coverage.full_pairs} pairs scheduled across the selected maps`));
    if (experiment.schedule?.length) {
        const details = element("details");
        details.dataset.section = "schedule";
        details.open = openSections?.has("schedule") ?? false;
        const table = experimentTable(["Pair", "Game", "Map", "Side 1", "Side 2"]);
        for (const planned of experiment.schedule) {
            const team = side => planned.players.filter(player => player.ally === side).map(player =>
                `${player.name || player.short_name}${player.profile ? "/" + player.profile : ""} (${player.faction})`).join(" + ");
            const row = table.tBodies[0].insertRow();
            for (const value of [planned.pair_index, planned.index, planned.map, team(0), team(1)]) {
                row.append(element("td", "", String(value)));
            }
        }
        details.append(element("summary", "", "Scheduled games"), experimentTableWrap(table));
        box.append(details);
    }
    if (experiment.provenance) {
        const provenance = experiment.provenance;
        const details = element("details"), list = element("dl", "artifact-fingerprints");
        details.dataset.section = "fingerprints";
        details.open = openSections?.has("fingerprints") ?? false;
        const checksumSource = provenance.archive_checksum_source === "engine_startup_log"
            ? "Engine startup log" : provenance.archive_checksum_source;
        const entries = [["Engine SHA-256", provenance.engine_sha256], ["Unitsync SHA-256", provenance.unitsync_sha256],
            ["Archive checksum source", checksumSource], ["Game archive checksum", provenance.game_archive_checksum]];
        if (provenance.archive_conflicts?.length) {
            entries.push(["Changed during run", provenance.archive_conflicts.join(", ")]);
        }
        for (const [map, checksum] of Object.entries(provenance.map_archive_checksums || {})) {
            entries.push([`${map} archive checksum`, checksum]);
        }
        for (const ai of provenance.ais || []) entries.push([`${ai.short_name} ${ai.version} SHA-256`, ai.sha256]);
        for (const [label, value] of entries) {
            list.append(element("dt", "", label), element("dd", "", String(value ?? "Unavailable")));
        }
        details.append(element("summary", "", "Engine and content fingerprints"), list);
        box.append(details);
    }
    const finalists = experiment.rankings || [];
    if (finalists.length) {
        const table = experimentTable(["Profile", "Wins", "Losses", "Draws", "Win rate", "Avg. game",
            "Script errors", "Engine errors", "Error-free", "Failures", "Timeouts",
            ...(experiment.mode === "sweep" ? ["Clean decided pairs", "Paired score"] : [])]);
        table.classList.add("experiment-standings");
        for (const row of finalists) {
            const tr = element("tr");
            const rate = typeof row.win_rate === "number" ? `${(100 * row.win_rate).toFixed(1)}%` : "—";
            const average = trackerClock(row.average_duration_seconds);
            const pairedRate = typeof row.paired_win_rate === "number" ? `${(100 * row.paired_win_rate).toFixed(1)}%` : "—";
            const pairedValues = experiment.mode === "sweep" ? [row.valid_pairs, pairedRate] : [];
            for (const value of [row.label, row.wins, row.losses, row.draws, rate, average, row.script_errors,
                row.engine_errors, row.error_free_games, row.failures, row.timeouts, ...pairedValues]) {
                tr.append(element("td", "", String(value ?? "—")));
            }
            table.tBodies[0].append(tr);
        }
        box.append(element("h3", "", "Leaderboard"), experimentTableWrap(table));
        const winner = finalists.find(row => row.id === experiment.best_candidate_id && row.promotable);
        if (experiment.promotable && winner) box.append(button(
            `Promote ${winner.label} to a profile`, () => experimentPromotion(experiment, winner), "action",
            "Copy the winning sweep value into a new editable profile. The source profile remains available, and the new one can be installed after review."
        ));
    }
    const games = experiment.games || [];
    if (games.length) {
        box.append(experimentMatchupMatrices(experiment, openSections, opponentColumns));
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
        details.dataset.section = "games";
        details.open = openSections?.has("games") ?? true;
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
    if (form) {
        form.form.hidden = running;
        form.disabled = running || form.form.dataset.pending === "true";
    }
    const experiment = snapshot?.operation === "experiment" && (snapshot.result?.experiment || snapshot.experiment);
    const {heading, progress, feedback, controls, live, results, pause, cancel, setupNote} = target.parts;
    setupNote.hidden = !running || !form;
    const activeMatchups = experiment && running ? (snapshot.active_matchups || []) : [];
    renderLiveGames(live, activeMatchups.map(game => ({...game, limit: experiment.minutes * 60})));
    const inHistory = experiment && state.experiments.history.some(item => item.id === experiment.id);
    const failed = snapshot?.operation === "experiment" && snapshot.state === "failed";
    heading.hidden = !experiment && !failed;
    let statusText = "";
    if (running && !experiment) statusText = snapshot.operation === "experiment"
        ? "Preparing experiment…" : "Another Studio operation is running.";
    if (failed && !experiment) {
        statusText = `Experiment failed. ${snapshot.message || snapshot.error || "Unknown error."}`;
    }
    if (experiment) {
        const games = `${experiment.completed_games} / ${experiment.scheduled_games} games`;
        const active = running ? ` · ${snapshot.active_games} active · ${snapshot.parallel_games} parallel limit` : "";
        const draining = (experiment.pause_requested || snapshot.parallel_games === 0) && snapshot.active_games > 0;
        const phase = experiment.paused ? "Paused" : snapshot.state === "running" && draining ? "Draining active games" : snapshot.state;
        const detail = experiment.paused ? " · Real-time budget still running"
            : running && snapshot.state === "running" ? "" : ` · ${snapshot.message || ""}`;
        statusText = `${phase === "failed" ? "Experiment failed" : phase} · ${games}${active}${detail}`;
    }
    if (progress.textContent !== statusText) progress.textContent = statusText;
    const statusError = state.match.statusError ? "Could not refresh experiment status. " + state.match.statusError : "";
    if (feedback.textContent !== statusError && (statusError || feedback.dataset.statusError)) {
        feedback.textContent = statusError;
        feedback.dataset.statusError = statusError;
    }
    const canCancel = running && snapshot.state === "running" && snapshot.operation === "experiment";
    const canPause = canCancel && experiment && snapshot.parallel_games > 0;
    const active = document.activeElement;
    const focusAfter = active === pause && !canPause || active === cancel && !canCancel;
    controls.hidden = !canCancel;
    pause.hidden = !canPause;
    cancel.hidden = !canCancel;
    if (focusAfter) (canCancel ? cancel : progress).focus({preventScroll: true});
    const pauseLabel = experiment?.paused || experiment?.pause_requested ? "Resume" : "Pause new games";
    if (pause.textContent !== pauseLabel) {
        pause.textContent = pauseLabel;
        describeControl(pause, StudioTips.action(pauseLabel));
    }
    const showing = experiment && (running || !inHistory);
    const signature = JSON.stringify([showing ? experiment.id : null, experiment?.state, experiment?.coverage,
        experiment?.completed_games, experiment?.games?.length, experiment?.stop_reason,
        experiment?.schedule?.length, Boolean(experiment?.provenance)]);
    if (results.dataset.signature === signature || results.contains(document.activeElement)) return;
    results.dataset.signature = signature;
    const sections = Array.from(results.querySelectorAll("details[data-section]"));
    const openSections = sections.length ? new Set(sections.filter(section => section.open).map(section => section.dataset.section)) : null;
    for (const name of ["matchups", "games"]) {
        if (openSections && !sections.some(section => section.dataset.section === name)) openSections.add(name);
    }
    const opponentColumns = new Map([...results.querySelectorAll("[data-matrix]")]
        .map(view => [view.dataset.matrix, view.dataset.firstOpponent]));
    results.replaceChildren(...(showing ? [experimentResults(experiment, openSections, opponentColumns)] : []));
}

function experimentStatusPanel() {
    const panel = element("div", "panel");
    panel.id = "experiment-status";
    const heading = element("h2", "", "Current experiment");
    const progress = element("p", "experiment-progress");
    progress.tabIndex = -1;
    const setupNote = element("p", "muted", "New experiment setup returns when the current operation ends.");
    const feedback = element("p", "inline-error");
    progress.setAttribute("role", "status");
    feedback.setAttribute("role", "status");
    const controls = element("div", "row");
    const live = element("section", "live-game-tracker");
    const results = element("div");
    results.addEventListener("focusout", () => setTimeout(updateExperimentStatus, 0));
    const action = (label, endpoint, payload) => button(label, async event => {
        const control = event.currentTarget;
        const current = state.match.snapshot;
        if (control.getAttribute("aria-disabled") === "true") return;
        control.setAttribute("aria-disabled", "true");
        feedback.textContent = "";
        try {
            const response = await api(endpoint, {...payload(current), id: current.id});
            if (state.match.snapshot?.id === current.id) {
                state.match.snapshot = response;
                updateExperimentStatus();
                pollMatch(true);
            }
        } catch (error) { feedback.textContent = errorText(error); }
        finally { control.removeAttribute("aria-disabled"); }
    });
    const pause = action("Pause new games", "/api/experiment/pause", snapshot => ({
        paused: !(snapshot.experiment?.paused || snapshot.experiment?.pause_requested)
    }));
    const cancel = action("Cancel experiment", "/api/match/cancel", () => ({}));
    controls.append(pause, cancel);
    panel.append(heading, progress, setupNote, feedback, controls, live, results);
    panel.parts = {heading, progress, feedback, controls, live, results, pause, cancel, setupNote};
    return panel;
}

function experimentHistoryDeletion() {
    const row = element("div", "row");
    const feedback = element("p", "muted");
    feedback.id = "experiment-delete-feedback";
    feedback.setAttribute("role", "status");
    const remove = async (all) => {
        const id = state.experiments.selected;
        const current = state.match.snapshot;
        const deletingCurrent = current?.operation === "experiment" && (all || current.id === id);
        if (deletingCurrent && ["running", "cancelling"].includes(current.state)) {
            feedback.textContent = "Wait for the current experiment to finish before deleting its results.";
            return;
        }
        const target = all ? "all saved experiment results" : "the selected experiment result";
        if (!confirm(`Permanently delete ${target}? Individual game logs and replays stay in Run storage.`)) return;
        one.disabled = both.disabled = true;
        feedback.textContent = "Deleting results…";
        try {
            const {failed = []} = await api("/api/experiment/delete", all ? {all: true} : {id});
            if (deletingCurrent && state.match.snapshot?.id === current.id && !failed.some(item => item.id === current.id)) {
                state.match.snapshot = null;
            }
            await loadExperimentHistory(false, all ? 0 : state.experiments.historyOffset);
            if (failed.length) {
                const detail = `Could not delete ${failed.length} saved ${failed.length === 1 ? "result" : "results"}. ${failed[0].error}`;
                const status = $("experiment-delete-feedback");
                if (status) {
                    status.classList.add("inline-error");
                    status.textContent = detail;
                } else message(detail, true);
            }
        } catch (error) {
            feedback.textContent = "Could not delete experiment results. " + errorText(error);
            one.disabled = both.disabled = false;
        }
    };
    const one = button("Delete selected result", () => remove(false), "action",
        "Remove the result currently selected in Experiment history.");
    const both = button("Delete all results", () => remove(true), "action",
        "Remove every saved result, including experiments on other history pages.");
    row.append(one, both, feedback);
    return row;
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
        const status = element("p", "muted", "Loading the saved experiment…");
        status.setAttribute("role", "status");
        result.replaceChildren(status);
        try {
            const {experiment} = await api("/api/experiment/history?" + new URLSearchParams({id: entry.id}));
            if (data.selected !== entry.id || !status.isConnected) return;
            result.replaceChildren(experimentResults(experiment));
        } catch (error) {
            if (data.selected === entry.id && status.isConnected) {
                status.textContent = "Could not load the saved experiment. " + errorText(error);
            }
        }
    };
    if (!data.history.some(item => item.id === data.selected)) data.selected = data.history[0].id;
    box.append(dropdown("Saved experiment", data.history.map(item => [item.id,
        `${item.mode === "sweep" ? "Sweep" : "Tournament"} · ${item.started_at} · ${item.state}`
    ]), data.selected, value => { data.selected = value; draw(); }), experimentHistoryDeletion(), result,
    historyPagination("experiment-history-pagination", () => data.historyOffset, () => data.historyNext,
        async offset => {
            const direction = offset > data.historyOffset ? "forward" : "backward";
            await loadExperimentHistory(false, offset);
            const selector = direction === "forward" ? "button:last-child" : "button:first-child";
            const buttons = $("experiment-history-pagination");
            if (document.activeElement === document.body && buttons) {
                const desired = buttons.querySelector(selector);
                (desired.disabled ? buttons.querySelector("button:not([disabled])") : desired)?.focus();
            }
        }));
    draw();
}

let experimentHistoryRequest = 0;
async function loadExperimentHistory(checkStatus = false, offset = state.experiments.historyOffset) {
    const data = state.experiments;
    const request = ++experimentHistoryRequest;
    try {
        if (checkStatus && !matchPoll) {
            state.match.snapshot = await api("/api/match/status");
            updateExperimentStatus();
            if (["running", "cancelling"].includes(state.match.snapshot.state)) pollMatch();
        }
        const result = await api("/api/experiment/history?" + new URLSearchParams({offset, limit: 25}));
        if (request !== experimentHistoryRequest) return;
        if (!result.experiments.length && offset > 0) return loadExperimentHistory(false, Math.max(0, offset - 25));
        data.history = result.experiments;
        data.historyOffset = offset;
        data.historyNext = result.next_offset ?? null;
        data.error = "";
    } catch (error) {
        if (request !== experimentHistoryRequest) return;
        data.error = errorText(error);
    }
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
    const live = experimentStatusPanel(), history = element("div", "panel");
    history.id = "experiment-history";
    target.append(live, history);
    updateExperimentStatus();
    renderExperimentHistory();
}
