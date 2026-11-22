"use strict";

const $ = id => document.getElementById(id);
const C = window.StudioConfig;
const S = window.StudioScripts;
const state = {
    projects: [], project: "", profile: "", files: [], page: "overview",
    layer: {}, factory: "", situation: "land", response: "", file: "",
    settings: null, defaultBarRoot: "", catalog: null, unitNames: new Map(), icons: {available: false, message: "", cache: new Map()}, saving: false, history: [], match: {content: null, cached: false, cacheKey: "", cacheError: "", game: "", map: "", first: "", second: "", faction1: "Armada", faction2: "Cortex", minutes: 120, skip: false, snapshot: null, statusError: ""}
};
const pages = {
    manage: ["AI install & remove", "Choose a BAR installation, then install or remove the selected AI.", "⇄"],
    overview: ["Overview", "Your strategy, at a glance.", "◫"],
    opening: ["Opening", "Shape the first units a new factory queues.", "◷"],
    production: ["Production", "Set relative preferences for each factory and income tier.", "▦"],
    economy: ["Economy", "Guide expansion, resource production, and construction.", "◈"],
    defense: ["Defense", "Configure defenses, building chains, and commander behavior.", "◇"],
    combat: ["Combat & response", "Tune responses to enemy roles and combat priorities.", "⌁"],
    units: ["Unit behavior", "Explore roles, attributes, and individual unit settings.", "⬡"],
    placement: ["Building placement", "Choose how much room the AI leaves around buildings.", "▧"],
    advanced: ["All files", "Inspect every profile file, including custom scripts.", "⚙"],
    match: ["Test match", "Run a smoke test and a headless match with installed AIs.", "▶"]
};
const groups = {
    production: ["factory.json", "factory_leg.json"],
    economy: ["economy.json", "economy_leg.json"],
    defense: ["build_chain.json", "build_chain_leg.json", "commander.json", "commander_leg.json"],
    combat: ["response.json", "behaviour.json", "behaviour_leg.json"],
    units: ["behaviour.json", "behaviour_leg.json", "behaviour_scav_units.json", "behaviour_extra_units.json"],
    placement: ["block_map.json"]
};
function element(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
}
function button(text, fn, cls = "action") {
    const b = element("button", cls, text);
    b.type = "button"; b.addEventListener("click", fn);
    return b;
}
function notice(text, warning = false) { return element("div", "notice" + (warning ? " warning" : ""), text); }
function errorText(error) { return error instanceof Error ? error.message : String(error); }
function message(text, error = false) {
    $("message").hidden = !text;
    $("message").className = "message" + (error ? " error" : "");
    $("message").textContent = text;
}
async function api(url, data, signal) {
    const response = await fetch(url, {
        method: data === undefined ? "GET" : "POST",
        headers: {"X-Workshop-Token": window.WORKSHOP_TOKEN, ...(data === undefined ? {} : {"Content-Type": "application/json"})},
        ...(data === undefined ? {} : {body: JSON.stringify(data)}),
        ...(signal ? {signal} : {})
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || `Request failed (${response.status})`);
    return result;
}
function selectedProject() { return state.projects.find(p => p.key === state.project); }
function dirty() { return state.files.filter(f => f.text !== f.original); }
function status() {
    const count = dirty().length;
    $("save-status").textContent = !state.profile ? "No profile loaded" : count ? `${count} unsaved ${count === 1 ? "file" : "files"}` : "All changes saved";
    $("save").disabled = !count || state.saving;
    $("undo").disabled = !state.history.length;
    $("project").disabled = state.saving;
    $("profile").disabled = state.saving;
    $("reload").disabled = state.saving || !state.profile;
    $("new-project").disabled = state.saving;
    $("clone").disabled = state.saving || !state.profile;
    $("review").disabled = !count;
    $("project-history").disabled = state.saving || !state.project;
    $("manage-project").disabled = state.saving;
}
function assign(file, text, record = true) {
    if (text === file.text) return;
    if (record) {
        state.history.push({path: file.path, before: file.text});
        if (state.history.length > 100) state.history.shift();
    }
    file.text = text;
    const source = document.querySelector("textarea[data-source]");
    if (source && state.file === file.path && source.value !== text) source.value = text;
    status();
}
function parse(file) { return C.parse(file.text); }
function find(path) { return state.files.find(f => f.path.replace(/\\/g, "/") === path); }
function selectOptions(select, choices, value) {
    select.replaceChildren();
    for (const [key, name] of choices) {
        const opt = element("option", "", name); opt.value = key; select.append(opt);
    }
    select.value = value;
}
function confirmDiscard() { return !dirty().length || confirm("Discard unsaved changes in this profile?"); }
function setSettings(settings) {
    if (state.settings?.bar_root !== settings?.bar_root || state.settings?.engine !== settings?.engine) {
        state.catalog = null; state.match.content = null;
        state.match.cached = false; state.match.cacheKey = ""; state.match.cacheError = "";
        if (state.match.snapshot?.operation === "scan" &&
            !["running", "cancelling"].includes(state.match.snapshot.state)) state.match.snapshot = null;
    }
    state.settings = settings;
}
function matchSettingsKey(settings) {
    return settings ? JSON.stringify([settings.bar_root, settings.engine]) : "";
}
async function loadCachedMatchContent() {
    const key = matchSettingsKey(state.settings);
    if (!key || key === state.match.cacheKey || isScanActive(state.match.snapshot)) return;
    state.match.cacheKey = key;
    state.match.cacheError = "";
    try {
        const result = await api("/api/match/cache", state.settings);
        if (matchSettingsKey(state.settings) !== key) return;
        state.match.content = result.content;
        state.match.cached = !!result.content;
        if (result.content && !state.match.snapshot) {
            state.match.snapshot = {
                operation: "scan", state: "completed", message: "Using saved scan results."
            };
        }
    } catch (error) {
        if (matchSettingsKey(state.settings) === key) state.match.cacheError = errorText(error);
    }
}
async function refreshState(project = state.project, profile = state.profile) {
    const data = await api("/api/state");
    state.projects = data.projects; setSettings(data.settings);
    await loadIconStatus();
    state.defaultBarRoot = data.default_bar_root || "";
    await loadCachedMatchContent();
    state.project = state.projects.some(p => p.key === project) ? project : state.projects[0]?.key || "";
    const projectInfo = selectedProject();
    state.profile = projectInfo?.profiles.some(p => p.key === profile) ? profile :
        projectInfo?.profiles.find(p => p.key === projectInfo.default_profile)?.key || projectInfo?.profiles[0]?.key || "";
    selectOptions($("project"), state.projects.map(p => [p.key, p.name]), state.project);
    selectOptions($("profile"), (projectInfo?.profiles || []).map(p => [p.key, p.name]), state.profile);
    $("clone").disabled = !state.profile;
}
let profileRequest = 0;
async function loadProfile() {
    const request = ++profileRequest;
    const {project, profile} = state;
    state.files = []; state.history = [];
    const current = () => request === profileRequest && state.project === project && state.profile === profile;
    if (project && profile) {
        const result = await api(`/api/profile?project=${encodeURIComponent(project)}&profile=${encodeURIComponent(profile)}`);
        if (!current()) return;
        state.files = result.files.map(f => ({...f, original: f.text}));
    }
    await refreshBaseline();
    if (!current()) return;
    status(); render();
}
async function refreshBaseline() {
    const {project, profile, files} = state;
    if (!files.length || selectedProject()?.history_error) return;
    try {
        const {paths} = await api("/api/history/diff", {project, names_only: true});
        const changed = new Set(paths);
        for (const file of files) file.baselineChanged = changed.has(`profiles/${profile}/${file.path}`);
    } catch (_) { /* History availability is reported in Project History. */ }
}
function baselineLabel(file, label) {
    return file?.baselineChanged ? label + " · Modified from baseline" : label;
}
function header(page) {
    const [title, subtitle] = pages[page], head = element("div", "heading"), left = element("div");
    left.append(element("div", "eyebrow", "Strategy workshop"), element("h1", "", title), element("p", "lead", subtitle));
    head.append(left); return head;
}
function showDialog(title, content) {
    $("dialog-title").textContent = title;
    $("dialog-content").replaceChildren(content);
    if (!$("dialog").open) $("dialog").showModal();
}
function textInput(label, value = "", placeholder = "") {
    const wrap = element("label", "field", label), input = element("input");
    input.value = value; input.placeholder = placeholder; wrap.append(input);
    return [wrap, input];
}
function dropdown(label, options, value, change) {
    const wrap = element("label", "field", label), select = element("select");
    selectOptions(select, options, value);
    select.addEventListener("change", () => change(select.value));
    wrap.append(select); return wrap;
}
function chooseFile(page) {
    const names = groups[page] || [];
    const families = [...new Set(names.map(name => name.replace(/(_leg|_scav_units|_extra_units)?\.json$/, "")))];
    const choices = state.files.filter(file => {
        const match = file.path.replace(/\\/g, "/").match(/^config\/([^/]+)\.json$/);
        return match && families.some(family => match[1] === family || match[1].startsWith(family + "_"));
    }).sort((a, b) => {
        const rank = file => {
            const name = file.path.replace(/\\/g, "/").split("/").pop();
            const exact = names.indexOf(name);
            return exact < 0 ? names.length : exact;
        };
        return rank(a) - rank(b) || a.path.localeCompare(b.path);
    });
    if (!choices.length) return null;
    let path = state.layer[page];
    if (!choices.some(f => f.path === path)) path = choices[0].path;
    state.layer[page] = path;
    return {file: choices.find(f => f.path === path), choices};
}
function layerControl(page, selected) {
    const wrapper = element("div", "row");
    const placement = page === "placement";
    const choices = selected.choices.map(f => [
        f.path,
        baselineLabel(f, placement ? "Building placement rules" : f.path.split("/").pop())
    ]);
    wrapper.append(dropdown(placement ? "Settings file to edit" : "Configuration layer", choices,
        selected.file.path, value => { state.layer[page] = value; state.factory = ""; state.response = ""; render(); }),
        button(placement ? "Preview settings from multiple files" : "Preview combined layers", () => layerPreview(selected.file, placement)));
    return wrapper;
}
function layerPreview(initial, placement = false) {
    const files = state.files.filter(file => /^config\/[^/]+\.json$/.test(file.path.replace(/\\/g, "/")));
    const order = [initial.path], box = element("div"), content = element("div");
    box.append(element("p", "muted", placement
        ? "Read-only preview of settings files combined in the order shown. Later files can replace earlier values. Unsaved edits are included."
        : "A read-only what-if preview. Add files in the exact order your script loads them; later files can override earlier ones. This includes unsaved edits."),
        notice(placement
            ? "This preview does not run the game startup rules. The game may load other files or choose different settings, so check the result in a game."
            : "The script is not interpreted. Game options, game-side files, fallback files and your selected DLL may change what actually loads. Verify the order against init.as and test in the engine.", true), content);
    const name = initial.path.split("/").pop();
    const family = Object.values(groups).flat().map(item => item.replace(/(_leg|_scav_units|_extra_units)?\.json$/, ""))
        .filter(item => name === item + ".json" || name.startsWith(item + "_"))
        .sort((a, b) => b.length - a.length)[0];
    const stem = family ? "config/" + family : initial.path.replace(/\.json$/, "");
    const base = files.find(file => file.path === stem + ".json");
    const variants = files.filter(file => file.path.startsWith(stem + "_"))
        .map(file => [file.path.split("/").pop(), file.path]);
    if (base && variants.length) {
        const example = element("div", "layer-example"), options = element("div", "row");
        example.append(element("h3", "", "Quick example"),
        element("p", "muted", placement
            ? "Choose which settings files to combine. This example does not check which files the game actually loads."
            : "Choose candidate files from this family. Their listed order is only an example, not the order in your script; reorder them after adding."));
        const checkboxes = variants.map(([name, path]) => {
            const label = element("label", "option-check"), input = element("input");
            input.type = "checkbox"; input.checked = path === initial.path;
            label.append(input, document.createTextNode(name)); options.append(label);
            return {input, path};
        });
        example.append(options, button("Use example order", () => {
            order.splice(0, order.length, base.path, ...checkboxes.filter(item => item.input.checked).map(item => item.path));
            draw();
        }));
        box.insertBefore(example, content);
    }
    function draw() {
        content.replaceChildren();
        const list = element("ol", "layer-order");
        order.forEach((path, index) => {
            const row = element("li", "layer-order-item");
            row.append(element("span", "grow", path));
            const up = button("Move up", () => { [order[index - 1], order[index]] = [order[index], order[index - 1]]; draw(); });
            const down = button("Move down", () => { [order[index + 1], order[index]] = [order[index], order[index + 1]]; draw(); });
            up.disabled = index === 0; down.disabled = index === order.length - 1;
            const remove = button("Remove", () => {order.splice(index, 1); draw();});
            up.setAttribute("aria-label", `Move ${path} at position ${index + 1} up`);
            down.setAttribute("aria-label", `Move ${path} at position ${index + 1} down`);
            remove.setAttribute("aria-label", `Remove ${path} at position ${index + 1}`);
            row.append(up, down, remove);
            list.append(row);
        });
        const picker = element("select");
        picker.setAttribute("aria-label", placement ? "Settings file to add" : "Configuration file to add");
        selectOptions(picker, files.map(file => [file.path, file.path]), files[0]?.path || "");
        const row = element("div", "row");
        row.append(picker, button(placement ? "Add settings file" : "Add file", () => {if (picker.value) {order.push(picker.value); draw();}}));
        content.append(element("h3", "", placement ? "Settings order" : "Preview load order"), list, row);
        if (!order.length) { content.append(notice("Add a file to see the preview.")); return; }
        try {
            const preview = C.previewLayers(order.map(path => files.find(file => file.path === path)));
            content.append(element("h3", "", placement ? "Combined settings" : "Combined values"),
                element("p", "muted", placement
                    ? "Open a section to see which file supplies each setting. Lists are replaced as a whole by later files; this preview does not edit anything."
                    : "Open a section to see values and their supplying file. Arrays are replaced as a whole; this view does not edit files."));
            if (preview.ignored.length) {
                const warning = notice(placement
                    ? `${preview.ignored.length} ${preview.ignored.length === 1 ? "setting could" : "settings could"} not be combined because the files use different kinds of value for the same setting.`
                    : `${preview.ignored.length} incoming ${preview.ignored.length === 1 ? "value was" : "values were"} ignored because an existing object cannot be replaced by a non-object.`, true);
                const details = element("details"), summary = element("summary", "", placement ? "Show settings not combined" : "Show ignored values"), body = element("div", "tree-body");
                for (const item of preview.ignored) body.append(element("p", "path", `${item.path.join(" / ") || "(root)"}: ${item.source} did not replace ${item.retained}`));
                details.append(summary, body); warning.append(details); content.append(warning);
            }
            content.append(previewNode(preview.root, placement ? "Combined settings" : "Combined configuration", []));
        } catch (error) { content.append(notice("Cannot build preview: " + errorText(error), true)); }
    }
    draw(); showDialog(placement ? "Preview settings from multiple files" : "Preview combined layers", box);
}
function previewNode(node, title, path) {
    title = unitLabel(title);
    const origin = node.source + (node.previous.length ? " · replaces earlier values from " + node.previous.map(item => item.source).join(" → ") : "");
    function previousValues() {
        const detail = element("details"), body = element("div", "tree-body");
        detail.append(element("summary", "", "Earlier values"), body);
        for (const item of node.previous) {
            body.append(element("div", "path", item.source), element("pre", "", JSON.stringify(item.value)));
        }
        return detail;
    }
    if (node.kind === "object") {
        const detail = element("details"), body = element("div", "tree-body");
        detail.append(element("summary", "", `${title}  ·  ${node.children.size}`), body);
        detail.addEventListener("toggle", () => {
            if (detail.open && !body.childNodes.length) {
                if (node.previous.length) {
                    body.append(element("p", "path", `Object from ${node.source} replaced an earlier value.`), previousValues());
                }
                else if (!node.children.size) body.append(element("p", "path", "Empty object from " + node.source));
                for (const [key, child] of node.children) body.append(previewNode(child, key, path.concat(key)));
            }
        });
        return detail;
    }
    const row = element("div", "tree-field"), label = element("div", "grow");
    label.append(element("div", "", title), element("div", "path", path.join(" / ")),
        element("div", "path", "Source: " + origin));
    if (node.previous.length) label.append(previousValues());
    const value = element("pre", "preview-value", JSON.stringify(node.value));
    row.append(label, value); return row;
}
function fieldInput(file, node, aria, callback) {
    const input = element("input");
    input.setAttribute("aria-label", aria);
    if (node.kind === "boolean") { input.type = "checkbox"; input.checked = node.value; }
    else {
        input.type = node.kind === "number" ? "number" : "text";
        if (node.kind === "number") input.step = "any";
        input.value = node.value;
    }
    input.addEventListener("change", () => {
        try {
            let value;
            if (node.kind === "boolean") value = input.checked;
            else if (node.kind === "number") {
                if (!input.value.trim() || !Number.isFinite(Number(input.value))) throw Error("Enter a valid number.");
                value = Number(input.value);
            } else value = input.value;
            const current = C.at(parse(file), node.path);
            assign(file, C.scalar(file.text, current, value));
            message("");
            if (callback) callback(value);
        } catch (error) {
            message(errorText(error), true);
            const current = C.at(parse(file), node.path);
            if (node.kind === "boolean") input.checked = current.value;
            else input.value = current.value;
        }
    });
    return input;
}
function treeNode(file, node, title, depth = 0, onChange = null, describe = null) {
    const info = describe ? describe(node.path, node.value, node.kind) : null;
    if (info && info.label) title = info.label;
    if (node.children) {
        const detail = element("details"), summary = element("summary", "", `${title}  ·  ${node.children.length}`);
        const body = element("div", "tree-body");
        detail.append(summary, body);
        detail.addEventListener("toggle", () => {
            if (detail.open && !body.childNodes.length) {
                if (info && info.description) body.append(element("p", "muted", info.description));
                for (const child of node.children) {
                    const key = String(child.key), label = friendlyUnitName(key) ? unitLabel(key) : key.replace(/_/g, " ");
                    body.append(treeNode(file, child.node, label, depth + 1, onChange, describe));
                }
            }
        });
        return detail;
    }
    const row = element("div", "tree-field"), label = element("label");
    label.append(element("div", "", title));
    if (info && info.description) label.append(element("div", "muted", info.description));
    else label.append(element("div", "path", node.path.join(" / ")));
    if (node.kind === "null") row.append(label, element("span", "muted", info ? "Not set (game default)" : "null · source view only"));
    else {
        const input = fieldInput(file, node, info && info.label ? info.label : node.path.join(" / "), onChange);
        const id = "field-" + Math.random().toString(36).slice(2);
        input.id = id; label.htmlFor = id; row.append(label, input);
        if (describe && info && info.valueDescription !== undefined) {
            const valueDescription = element("div", "muted");
            const updateDescription = () => {
                const current = describe(node.path, input.type === "number" ? Number(input.value) : input.value, node.kind);
                valueDescription.textContent = current?.valueDescription || "";
            };
            input.addEventListener("change", updateDescription);
            updateDescription();
            label.append(valueDescription);
        }
        if (node.kind === "string") {
            const name = element("div", "muted");
            const updateName = () => { name.textContent = friendlyUnitName(input.value); };
            input.addEventListener("change", updateName); updateName(); label.append(name);
        }
    }
    return row;
}
function overview(target) {
    const info = element("div", "panel");
    info.append(element("div", "eyebrow", "CURRENT PROFILE"),
        element("h2", "", `${selectedProject()?.name || "No project"} / ${state.profile || "No profile"}`),
        element("p", "muted", "Start with a working profile, make one change, then save and test it in BAR."));
    const actions = element("div", "row");
    actions.append(button("Create AI from this project", cloneProject), button("AI install & remove", () => navigate("manage")), button("Test match", () => navigate("match")));
    info.append(actions);
    if (selectedProject()?.history_error) info.append(notice("Project history unavailable: " + selectedProject().history_error, true));
    info.append(notice("Compatibility is not verified against the selected DLL. Settings and scripts can behave differently between AI versions."));
    target.append(info);
    const grid = element("div", "grid");
    for (const key of ["opening", "production", "economy", "defense", "combat", "units", "placement", "advanced"]) {
        const [title, description, icon] = pages[key];
        const card = button("", () => navigate(key), "card");
        card.append(element("span", "card-arrow", "↗"), element("span", "card-icon", icon), element("h3", "", title), element("p", "", description));
        grid.append(card);
    }
    target.append(grid);
}
function opening(target) {
    let resolved;
    try { resolved = S.resolve(state.files, "script/misc/commander.as"); }
    catch (error) { target.append(notice(errorText(error), true)); return; }
    if (!resolved) { target.append(notice("This profile does not contain the supported opening script. Use All files to inspect its scripts.", true)); return; }
    const file = resolved.file;
    const panel = element("div", "panel");
    if (resolved.chain.length > 1) panel.append(notice(`Following a forwarding include: ${resolved.chain.join(" → ")}. This edits the included file; other scripts may also use it. Actual runtime behavior is not verified.`));
    catalogControls(panel);
    const editor = element("div");
    panel.append(editor);
    if (!window.StudioOpenings) panel.append(notice("Opening editor could not be loaded.", true));
    else window.StudioOpenings.mount(editor, {
        text: file.text, unitName: friendlyUnitName,
        unitIcon,
        onChange: text => { assign(file, text); message(""); }
    });
    panel.append(element("div", "source-bar", "Script source: " + file.path + " · Custom script that cannot be represented safely remains unchanged."));
    target.append(panel);
}
function catalogControls(panel) {
    const catalog = state.catalog;
    const row = element("div", "row");
    row.append(button(catalog ? "Change unit catalog" : "Load unit catalog", catalogDialog));
    if (catalog) row.append(element("p", "muted", `${catalog.game} · ${catalog.units.length} units`));
    panel.append(row);
    if (state.icons.message) panel.append(element("p", "muted", state.icons.message));
    if (catalog) {
        panel.append(notice("Names come from the last scanned game, not necessarily the current match or installation. This illustrative parse enables optional units in a simulated empty match and samples randomized definitions deterministically. Game options, runtime randomness, scripts and parser errors can change available units or build options.", true));
        if (catalog.warnings.length) {
            const details = element("details"), body = element("div", "tree-body");
            details.append(element("summary", "", `${catalog.warnings.length} catalog warning(s)`), body);
            for (const warning of catalog.warnings) body.append(element("p", "muted", warning));
            panel.append(details);
        }
    } else panel.append(element("p", "muted", "Load a selected installed game to browse unit names and its illustrative factory build options."));
}
function catalogUnit(id) {
    return state.catalog?.byId.get(String(id).toLowerCase());
}
function friendlyUnitName(id) {
    const key = String(id).toLowerCase();
    return catalogUnit(key)?.name || state.unitNames.get(key) || "";
}
function unitLabel(id) {
    const name = friendlyUnitName(id);
    return name ? `${name} · ${id}` : id;
}
async function loadIconStatus() {
    for (const promise of state.icons.cache.values()) {
        promise.then(url => { if (url) URL.revokeObjectURL(url); });
    }
    state.icons = {available: false, message: "", cache: new Map()};
    try {
        const result = await api("/api/unit-icons");
        state.icons.available = result.available;
        state.icons.message = result.message;
    } catch (error) {
        state.icons.message = "Could not load unit icons: " + errorText(error);
    }
}
function unitIcon(id) {
    const key = String(id).toLowerCase();
    const image = element("img", "unit-icon");
    image.alt = "";
    image.hidden = true;
    if (!state.icons.available || !/^[a-z0-9_]{1,128}$/.test(key)) return image;
    if (!state.icons.cache.has(key)) {
        state.icons.cache.set(key, (async () => {
            const response = await fetch(`/api/unit-icon/${encodeURIComponent(key)}`, {
                headers: {"X-Workshop-Token": window.WORKSHOP_TOKEN}
            });
            if (response.status === 404) return null;
            if (!response.ok) {
                const result = await response.json();
                throw Error(result.error || `Request failed (${response.status})`);
            }
            return URL.createObjectURL(await response.blob());
        })().catch(error => {
            message(`Could not load icon for ${key}: ${errorText(error)}`, true);
            return null;
        }));
    }
    state.icons.cache.get(key).then(url => {
        if (url) { image.src = url; image.hidden = false; }
    });
    return image;
}
async function loadUnitNames() {
    const response = await fetch("/data/units.json");
    if (!response.ok) throw Error(`Could not load bundled unit names (${response.status}).`);
    const data = await response.json();
    const names = data?.units?.names;
    if (!names || typeof names !== "object" || Array.isArray(names) ||
        !Object.entries(names).every(([key, name]) => key && typeof name === "string")) {
        throw Error("Bundled unit names have an invalid format.");
    }
    state.unitNames = new Map(Object.entries(names).map(([key, name]) => [key.toLowerCase(), name]));
}
function setCatalog(catalog) {
    state.catalog = catalog ? {...catalog, byId: new Map(catalog.units.map(unit => [unit.id.toLowerCase(), unit]))} : null;
}
function installationDialog() {
    if ($("dialog").open) $("dialog").close();
    navigate("manage");
}
function catalogDialog() {
    const box = element("div"), feedback = element("p", "muted"), selection = element("div");
    box.append(element("p", "muted", "Choose an installed game. The engine reads its definitions in an isolated process; the first scan may take several minutes."),
        notice("This is not a match simulation. All optional units are requested in an empty simulated match, and randomized definitions are sampled deterministically. Missing definitions, game options and runtime changes can affect the result.", true));
    if (!state.settings) {
        box.append(notice("Choose and save a BAR installation and engine first.", true),
            button("Installation settings", installationDialog));
        showDialog("Unit catalog", box); return;
    }
    const settings = {...state.settings};
    box.append(selection, feedback);
    showDialog("Unit catalog", box);
    (async () => {
        try {
            feedback.textContent = "Finding installed games...";
            const data = await api("/api/sources", {bar_root: settings.bar_root});
            const games = [...new Set(data.sources.map(source => source.game_name).filter(Boolean))];
            if (!games.length) throw Error("No packaged games were found. Unpacked sources cannot currently be selected for this catalog.");
            const label = element("label", "field", "Installed game"), select = element("select");
            selectOptions(select, [["", "Choose a game"], ...games.map(game => [game, game])], "");
            label.append(select);
            let job = null;
            const start = button("Read game units", async () => {
                if (!select.value) {feedback.textContent = "Choose an installed game."; return;}
                start.disabled = true; select.disabled = true;
                job = null;
                try {
                    job = await api("/api/catalog/start", {...settings, game: select.value});
                    feedback.textContent = "Reading unit definitions... You can cancel this operation.";
                    cancel.disabled = false;
                    for (;;) {
                        await new Promise(resolve => setTimeout(resolve, 1500));
                        const snapshot = await api("/api/match/status");
                        if (snapshot.id !== job.id) throw Error("Another operation started before catalog results were retrieved.");
                        feedback.textContent = `${snapshot.state}: ${snapshot.message || ""}`;
                        if (["running", "cancelling"].includes(snapshot.state)) continue;
                        if (snapshot.state !== "completed") throw Error(snapshot.error || snapshot.message || "Catalog did not complete.");
                        if (!state.settings || state.settings.bar_root !== settings.bar_root || state.settings.engine !== settings.engine) {
                            throw Error("Installation settings changed while the catalog was loading; scan again.");
                        }
                        const catalog = snapshot.result.catalog;
                        setCatalog(catalog);
                        render();
                        feedback.textContent = `Loaded ${catalog.units.length} units. ${catalog.warnings.length} warning(s).`;
                        use.disabled = false; break;
                    }
                } catch (error) { feedback.textContent = errorText(error); }
                finally { start.disabled = false; select.disabled = false; cancel.disabled = true; }
            }, "button primary");
            const cancel = button("Cancel catalog scan", async () => {
                try { await api("/api/catalog/cancel", {id: job.id}); cancel.disabled = true; }
                catch (error) { feedback.textContent = errorText(error); }
            });
            cancel.disabled = true;
            const use = button("Use catalog", () => { $("dialog").close(); render(); });
            use.disabled = true;
            selection.append(label, element("p", "muted", "If this dialog is closed while scanning, cancel from Test match."), start, cancel, use);
            feedback.textContent = "Choose the game version to inspect.";
        } catch (error) { feedback.textContent = errorText(error); }
    })();
}
function production(target, selected) {
    const file = selected.file, root = parse(file), factories = C.at(root, ["factory"]);
    const panel = element("div", "panel"); panel.append(layerControl("production", selected));
    panel.append(notice("This is one source layer, not a merged effective view. Later enabled layers can override values. Weights are relative, not guaranteed percentages."));
    catalogControls(panel);
    if (!factories || factories.kind !== "object" || !factories.children.length) {
        panel.append(notice("No factory definitions in this layer.", true)); target.append(panel); return;
    }
    if (!factories.children.some(e => e.key === state.factory)) state.factory = factories.children[0].key;
    const row = element("div", "row");
    row.append(dropdown("Factory", factories.children.map(e => [e.key, unitLabel(e.key)]), state.factory, value => { state.factory = value; render(); }),
        dropdown("Situation", [["land", "Land"], ["water", "Water"], ["air", "Enemy air"]], state.situation, value => { state.situation = value; render(); }));
    row.append(unitIcon(state.factory));
    panel.append(row);
    const fac = C.at(root, ["factory", state.factory]), units = C.at(fac, ["unit"]), group = C.at(fac, [state.situation]);
    const thresholds = C.at(fac, ["income_tier"]);
    if (thresholds?.kind === "array") {
        panel.append(element("h3", "", "Income thresholds"));
        const inputs = element("div", "row");
        for (const item of thresholds.children) {
            if (item.node.kind !== "number") continue;
            const label = element("label", "field", `Tier ${item.key + 1}`);
            label.append(fieldInput(file, item.node, `${state.factory} tier ${item.key + 1} income`)); inputs.append(label);
        }
        panel.append(inputs);
    }
    panel.append(element("h3", "", "Production preferences"));
    if (!units || units.kind !== "array" || !group || group.kind !== "object") {
        panel.append(notice("This situation or unit list is not present in this layer. Inspect other layers or All files.", true));
        target.append(panel); return;
    }
    const tiers = group.children.filter(e => /^tier\d+$/.test(e.key) && e.node.kind === "array")
        .sort((a, b) => Number(a.key.slice(4)) - Number(b.key.slice(4)));
    if (!tiers.length || tiers.some(e => e.node.children.length !== units.children.length)) {
        panel.append(notice("The tier lists are missing or do not align with the unit list. Use All files to inspect them.", true));
        target.append(panel); return;
    }
    const wrap = element("div", "table-wrap"), table = element("table"), head = element("tr");
    head.append(element("th", "", "Unit ID"));
    for (const tier of tiers) head.append(element("th", "", `Tier ${tier.key.slice(4)}`));
    head.append(element("th", "", "")); table.append(head);
    units.children.forEach((unit, i) => {
        const tr = element("tr"), name = element("td", "unit-name", unit.node.value);
        name.prepend(unitIcon(unit.node.value));
        const catalogEntry = catalogUnit(unit.node.value), factoryEntry = catalogUnit(state.factory);
        const friendlyName = friendlyUnitName(unit.node.value);
        if (friendlyName) name.append(element("div", "unit-sub", friendlyName));
        if (state.catalog && !catalogEntry) name.append(element("div", "unit-sub", "Not found in catalog"));
        else if (factoryEntry?.builds && !factoryEntry.builds.some(id => id.toLowerCase() === String(unit.node.value).toLowerCase())) {
            name.append(element("div", "unit-sub", "Not listed in this factory's catalog options"));
        }
        tr.append(name);
        for (const tier of tiers) {
            const td = element("td"), entry = tier.node.children[i].node;
            td.append(entry.kind === "number" ? fieldInput(file, entry, `${unit.node.value} ${tier.key} weight`) : element("span", "muted", "Unsupported"));
            tr.append(td);
        }
        const td = element("td");
        td.append(button("Remove", () => {
            if (!confirm(`Remove ${unit.node.value} from this factory and every tier in this file?`)) return;
            try { assign(file, C.changeFactoryUnit(file.text, state.factory, "", i)); render(); }
            catch (error) { message(errorText(error), true); }
        }));
        tr.append(td); table.append(tr);
    });
    wrap.append(table); panel.append(wrap);
    const factoryEntry = catalogUnit(state.factory);
    if (state.catalog && !factoryEntry) panel.append(notice("This factory was not found in the catalog; its build options are unknown.", true));
    else if (state.catalog && factoryEntry.builds === null) panel.append(notice("This factory's catalog build options could not be read.", true));
    else if (factoryEntry?.builds) {
        const available = factoryEntry.builds.filter(id => !units.children.some(item => String(item.node.value).toLowerCase() === id.toLowerCase()));
        if (available.length) {
            const picker = element("select"), choiceLabel = element("label", "field", "Catalog build option"), add = element("div", "row");
            selectOptions(picker, available.map(id => [id, unitLabel(id)]), available[0]);
            choiceLabel.append(picker);
            add.append(choiceLabel, button("Add catalog unit", () => {
                try {
                    assign(file, C.changeFactoryUnit(file.text, state.factory, picker.value, null));
                    message("Unit added with zero weights. Confirm its availability for your match."); render();
                } catch (error) { message(errorText(error), true); }
            }));
            panel.append(add);
        } else panel.append(element("p", "muted", "No additional build options are listed for this factory in the catalog."));
    }
    const addRow = element("div", "row"), [label, input] = textInput("Add unit ID", "", "e.g. armpw");
    addRow.append(label, button("Add unit", () => {
        try {
            assign(file, C.changeFactoryUnit(file.text, state.factory, input.value.trim(), null));
            message("Unit added with zero weights in each existing tier. Verify that this factory can build it.");
            render();
        } catch (error) { message(errorText(error), true); }
    }));
    panel.append(addRow, element("p", "muted", "Catalog suggestions are not proof of runtime buildability. You can also enter an ID manually."));
    panel.append(element("div", "source-bar", "Editing: " + file.path));
    scriptSettings(panel, "script/manager/factory.as", "Factory switching", [
        ["Switch threshold lower bound", "AiRandom\\s*\\(\\s*", "\\s*,", "integer", 0, null],
        ["Switch threshold upper bound", "AiRandom\\s*\\(\\s*\\d+\\s*,\\s*", "\\s*\\)\\s*\\*\\s*SECOND", "integer", 0, null]
    ]);
    target.append(panel);
}
function responseView(panel, file, root) {
    const response = C.at(root, ["response"]);
    if (!response || response.kind !== "object") return false;
    const roles = response.children.filter(e => e.node.kind === "object");
    if (!roles.length) return false;
    if (!roles.some(e => e.key === state.response)) state.response = roles[0].key;
    panel.append(dropdown("Friendly response role", roles.map(e => [e.key, e.key]), state.response, value => {state.response = value; render();}));
    const current = C.at(response, [state.response]);
    const vs = C.at(current, ["vs"]), ratio = C.at(current, ["ratio"]), importance = C.at(current, ["importance"]);
    if (!vs || !ratio || !importance || [vs, ratio, importance].some(n => n.kind !== "array") ||
        vs.children.length !== ratio.children.length || vs.children.length !== importance.children.length) {
        panel.append(notice("Response lists are missing or misaligned. Inspect them in All files.", true)); return true;
    }
    const table = element("table"), row = element("tr");
    for (const name of ["Enemy role", "Investment ratio", "Response importance", ""]) row.append(element("th", "", name));
    table.append(row);
    vs.children.forEach((entry, i) => {
        const tr = element("tr");
        for (const node of [entry.node, ratio.children[i].node, importance.children[i].node]) {
            const td = element("td");
            td.append(["string", "number"].includes(node.kind) ? fieldInput(file, node, node.path.join(" ")) : element("span", "muted", "Unsupported"));
            tr.append(td);
        }
        const remove = element("td");
        remove.append(button("Remove", () => {
            if (!confirm("Remove this enemy response from all three lists?")) return;
            try {
                assign(file, C.editArrays(file.text, ["vs", "ratio", "importance"].map(key =>
                    ({path: ["response", state.response, key], remove: i}))));
                render();
            } catch (error) { message(errorText(error), true); }
        }));
        tr.append(remove);
        table.append(tr);
    });
    const wrap = element("div", "table-wrap"); wrap.append(table); panel.append(wrap);
    const add = element("div", "row");
    const [roleLabel, roleInput] = textInput("Enemy role", "", "e.g. air");
    const [ratioLabel, ratioInput] = textInput("Investment ratio", "1");
    const [importanceLabel, importanceInput] = textInput("Importance", "0");
    add.append(roleLabel, ratioLabel, importanceLabel, button("Add response", () => {
        try {
            const role = roleInput.value.trim(), investment = Number(ratioInput.value), weight = Number(importanceInput.value);
            const currentRoles = C.at(parse(file), ["response", state.response, "vs"]);
            if (!role || currentRoles.children.some(e => e.node.value === role)) throw Error("Enter a new enemy role.");
            if (!ratioInput.value.trim() || !importanceInput.value.trim() || !Number.isFinite(investment) || !Number.isFinite(weight)) throw Error("Enter valid numeric values.");
            assign(file, C.editArrays(file.text, [
                {path: ["response", state.response, "vs"], add: role},
                {path: ["response", state.response, "ratio"], add: investment},
                {path: ["response", state.response, "importance"], add: weight}
            ]));
            message("Response added. Verify this role exists in the selected runtime."); render();
        } catch (error) { message(errorText(error), true); }
    }));
    panel.append(add);
    panel.append(notice("Importance is a relative response weight, not a percentage. Ratios and other production rules also matter; exact behavior depends on your AI version."));
    return true;
}
function economyView(panel, file, root) {
    const quick = [
        [["economy", "mex_up"], "Simultaneous metal extractor upgrades", "Maximum upgrades the AI can attempt at once."],
        [["economy", "cluster_range"], "Metal cluster range", "Distance in elmos used when grouping metal spots."],
        [["economy", "goal_exec"], "Target construction time", "Target time in seconds used when assigning builders."],
        [["economy", "buildpower"], "Mobile buildpower ratio", "Target ratio of mobile buildpower to metal income."],
        [["economy", "eps_step"], "Team-size scaling", "Scales certain economy decisions with team size."],
        [["economy", "mex_max", 0], "Metal spot cap", "Fraction of the map's spots; the adjacent ally-cap flag is in the full settings."],
        [["economy", "energy", "min_income"], "Minimum generator income", "Used in generator filtering by the inspected source."],
        [["economy", "energy", "cost_ratio"], "Generator cost ratio", "Its practical effect depends on the selected AI version."]
    ];
    const found = quick.map(([path, label, help]) => ({node: C.at(root, path), label, help})).filter(x => x.node?.kind === "number");
    if (!found.length) return;
    panel.append(element("h3", "", "Common economy settings"));
    for (const item of found) {
        const row = element("div", "tree-field"), label = element("label");
        label.append(element("div", "", item.label), element("div", "muted", item.help));
        row.append(label, fieldInput(file, item.node, item.label)); panel.append(row);
    }
    panel.append(notice("Generator selection differs between BARb versions. A setting being present does not guarantee that the selected DLL uses it."));
}
function unitsView(panel, file, root) {
    const units = C.at(root, ["behaviour"]);
    if (!units || units.kind !== "object" || !units.children.length) return;
    catalogControls(panel);
    let selected = state.unit;
    if (!units.children.some(e => e.key === selected)) selected = units.children[0].key;
    state.unit = selected;
    panel.append(dropdown("Unit ID", units.children.map(e => [e.key, unitLabel(e.key)]), selected, value => { state.unit = value; render(); }));
    panel.append(unitIcon(selected));
    const unit = C.at(units, [selected]);
    if (!unit || unit.kind !== "object") return;
    for (const key of ["role", "attribute"]) {
        const list = C.at(unit, [key]);
        if (!list || list.kind !== "array" || list.children.some(e => e.node.kind !== "string")) continue;
        panel.append(element("h3", "", key === "role" ? "Roles" : "Attributes"));
        if (key === "role") panel.append(element("p", "muted", "The first role is the primary role; later entries describe additional enemy roles in the inspected loader."));
        const rows = element("div");
        list.children.forEach((entry, index) => {
            const row = element("div", "row");
            row.append(fieldInput(file, entry.node, `${selected} ${key} ${index + 1}`),
                button("Remove", () => {
                    if (key === "role" && list.children.length === 1) { message("A unit needs a primary role.", true); return; }
                    try { assign(file, C.editArrays(file.text, [{path: ["behaviour", selected, key], remove: index}])); render(); }
                    catch (error) { message(errorText(error), true); }
                }));
            rows.append(row);
        });
        const [label, input] = textInput(`Add ${key === "role" ? "role" : "attribute"}`, "");
        rows.append(element("br"), label, button("Add", () => {
            const value = input.value.trim();
            const currentList = C.at(parse(file), ["behaviour", selected, key]);
            if (!value || currentList.children.some(e => e.node.value === value)) { message("Enter a new, non-duplicate value.", true); return; }
            try { assign(file, C.editArrays(file.text, [{path: ["behaviour", selected, key], add: value}])); message("Added. Verify the name exists in the selected runtime."); render(); }
            catch (error) { message(errorText(error), true); }
        }));
        panel.append(rows);
    }
    panel.append(element("h3", "", "Selected unit settings"));
    for (const child of unit.children.filter(e => !["role", "attribute"].includes(e.key))) {
        panel.append(treeNode(file, child.node, String(child.key).replace(/_/g, " ")));
    }
}
function defenseView(panel, file, root) {
    const chains = C.at(root, ["build_chain"]);
    if (!chains || chains.kind !== "object") return;
    const categories = chains.children.filter(e => e.node.kind === "object" && e.node.children.length);
    if (!categories.length) return;
    if (!categories.some(e => e.key === state.chainCategory)) state.chainCategory = categories[0].key;
    const category = C.at(chains, [state.chainCategory]);
    const buildings = category.children.filter(e => e.node.kind === "object");
    if (!buildings.length) return;
    if (!buildings.some(e => e.key === state.chainUnit)) state.chainUnit = buildings[0].key;
    const controls = element("div", "row");
    controls.append(dropdown("Completed building category", categories.map(e => [e.key, e.key]), state.chainCategory,
        value => {state.chainCategory = value; state.chainUnit = ""; render();}),
        dropdown("Completed building ID", buildings.map(e => [e.key, unitLabel(e.key)]), state.chainUnit,
            value => {state.chainUnit = value; render();}));
    controls.append(unitIcon(state.chainUnit));
    panel.append(element("h3", "", "On building completed"), controls);
    const hub = C.at(root, ["build_chain", state.chainCategory, state.chainUnit, "hub"]);
    if (!hub || hub.kind !== "array") {
        panel.append(notice("No supported hub chains exist for this building. Other settings remain available in the tree.")); return;
    }
    panel.append(element("p", "muted", "Each chain contains building actions and optional conditions. Positions are relative to the completed building; exact behavior depends on the runtime."));
    hub.children.forEach((chain, index) => {
        const card = element("div", "chain-card");
        card.append(element("h3", "", `Chain ${index + 1}`));
        if (chain.node.kind !== "array") {card.append(notice("Unsupported chain; use the settings tree.", true)); panel.append(card); return;}
        chain.node.children.forEach((step, stepIndex) => {
            const action = element("div", "chain-step");
            action.append(element("div", "chain-number", String(stepIndex + 1)));
            const body = element("div", "grow");
            if (step.node.kind !== "object") body.append(notice("Unsupported action; use the settings tree.", true));
            else {
                for (const key of ["unit", "category", "offset", "condition", "priority"]) {
                    const value = C.at(step.node, [key]);
                    if (value) body.append(treeNode(file, value, key.replace(/_/g, " ")));
                }
            }
            action.append(body); card.append(action);
        });
        const [label, input] = textInput("Add building unit ID", "", "e.g. armbeamer");
        const [offsetLabel, offset] = textInput("Front offset (elmos)", "80");
        const row = element("div", "row");
        row.append(label, offsetLabel, button("Add defense action", () => {
            try {
                const unit = input.value.trim(), distance = Number(offset.value);
                if (!unit || !offset.value.trim() || !Number.isFinite(distance)) throw Error("Enter a unit ID and numeric offset.");
                assign(file, C.appendArray(file.text, chain.node.path, {unit, category: "defence", offset: {front: distance}}));
                message("Action added. Verify that the builder and game version support this unit."); render();
            } catch (error) { message(errorText(error), true); }
        }));
        card.append(row); panel.append(card);
    });
}
const placementGroupNames = {
    fac_land: "Land factories",
    fac_land2: "Other land factories",
    fac_land_t1: "Basic land factories",
    fac_land_t2: "Advanced land factories",
    fac_air: "Air factories",
    fac_water: "Water factories",
    fac_strider: "Factories for very large units",
    solar: "Solar power plants",
    advsolar: "Advanced solar power plants",
    wind: "Wind and moving-water power plants",
    geo: "Power plants that use ground heat",
    geo2: "Advanced power plants that use ground heat",
    fusion: "Very high-output power plants",
    singu: "Top-tier power plants",
    pylon: "Power-sharing towers",
    store: "Resource storage buildings",
    mex: "Buildings that collect metal",
    mex2: "Advanced metal-collecting buildings",
    converter: "Buildings that turn power into metal",
    def_low: "Basic defensive buildings",
    def_mid: "Medium defensive buildings",
    def_hvy: "Strong defensive buildings",
    def_air: "Buildings that defend against aircraft",
    caretaker: "Buildings that help construction",
    superweapon: "Buildings with special attacks",
    protector: "Defenses that protect a wide area",
    strider: "Very large units",
    small: "Small buildings",
    wall: "Walls",
    _default_: "Other buildings"
};
const placementCategories = {
    factory: "Factories that build units",
    mex: "Buildings that collect metal",
    geo: "Power plants that use heat from the ground",
    pylon: "Towers that share power",
    convert: "Buildings that turn power into metal",
    engy_low: "Low-output power plants",
    engy_mid: "Medium-output power plants",
    engy_high: "High-output power plants",
    def_low: "Basic defensive buildings",
    def_mid: "Medium defensive buildings",
    def_high: "Strong defensive buildings",
    special: "Other special-purpose buildings",
    nano: "Units that help build",
    terra: "Units that reshape the ground",
    unknown: "Other or unclassified buildings"
};
function placementGroupLabel(key) {
    return placementGroupNames[key] || `Additional building group (${key.replace(/_/g, " ")})`;
}
function placementValueHelp(field, value) {
    if (field === "type" && value === "rectangle") return "A box-shaped area.";
    if (field === "type" && value === "circle") return "A round area.";
    if (field === "type" || field === "ignore" || field === "not_ignore") {
        return placementCategories[value] || (value === "all" ? "Applies to every building category." :
            value === "none" ? "Does not apply to any building category." : "An unrecognized building category; this may be a custom game value.");
    }
    if (field === "radius" && value === "explosion") return "Uses a radius based on the building's explosion area.";
    if (field === "radius" && value === "expl_ally") return "Uses a radius that scales with the number of allied players.";
    if (field === "radius" && typeof value === "number") return "Radius in steps; each step represents 16 units of in-game distance.";
    if (field === "radius") return "Use a number, or the game presets \"explosion\" and \"expl_ally\".";
    return "";
}
function placementInfo(path, value) {
    const parts = path.map(String), buildingIndex = parts.indexOf("building");
    if (parts[0] === "terrain") {
        const terrainDescriptions = {
            analyze: ["Unit used to inspect terrain", "The game unit definition used to inspect the map."],
            lake_tiles: ["Lake size threshold", "Water-area size, measured in map squares, used to distinguish lakes from seas."],
            lake_width_tiles: ["Lake width threshold", "Water-area width, measured in map squares, used to distinguish lakes from seas."],
            area_min_tiles: ["Smallest recognized area", "A connected map region must contain at least this many map squares to count as an area."],
            areas_merge_size: ["When map areas join", "A threshold the game uses when deciding whether to combine nearby map areas."],
            areas_merge_altitude: ["Height difference for joining areas", "A threshold the game uses when combining areas at different heights."]
        };
        if (parts.length === 1) return {label: "Map terrain rules", description: "These settings help the AI classify land, lakes, and seas; they do not set the space around a building."};
        const info = terrainDescriptions[parts[1]];
        return info ? {label: info[0], description: info[1]} :
            {label: parts[parts.length - 1].replace(/_/g, " "), description: "A game setting that affects how the AI reads the map."};
    }
    if (buildingIndex < 0) return null;
    if (parts.length === buildingIndex + 1) {
        return {label: "Building placement settings", description: "Rules that help the AI decide where buildings can fit."};
    }
    const section = parts[buildingIndex + 1];
    if (section === "instance") {
        if (parts.length === buildingIndex + 2) {
            return {label: "Buildings in each group", description: "Connects each rule group to the names the game uses for those buildings."};
        }
        if (parts.length === buildingIndex + 3) {
            return {label: placementGroupLabel(parts[buildingIndex + 2]), description: "The game unit names assigned to this building group."};
        }
        return {label: "Game unit name", description: "This is the name the game uses for a building or unit."};
    }
    if (section !== "class_land" && section !== "class_water") {
        return {label: section.replace(/_/g, " "), description: "A game-specific building setting. Its exact effect may depend on the game version."};
    }
    if (parts.length === buildingIndex + 2) {
        return {
            label: section === "class_land" ? "Land building rules" : "Water building rules",
            description: section === "class_land"
                ? "Spacing rules for buildings placed on land."
                : "Rules for buildings placed in water. When a water rule exists, it takes priority over the land rule."
        };
    }
    const groupIndex = buildingIndex + 2;
    if (parts.length === groupIndex + 1) {
        const key = parts[groupIndex];
        return {label: placementGroupLabel(key), description: `Internal group name: "${key}". The rule is used by the game units listed above.`};
    }
    const field = parts[groupIndex + 1], index = parts[groupIndex + 2];
    const descriptions = {
        type: ["Spacing area and building category", "The first value chooses a box or round area. The second classifies the building so spacing rules can distinguish building types."],
        offset: ["Position adjustment", "Moves the spacing area relative to the building. The first value shifts left or right; the second shifts toward the building's front or back."],
        size: ["Building area size", "Sets the width and depth of the building area before extra clearance. If omitted, the game uses the building's own size."],
        yard: ["Extra clearance", "Adds empty space around the building area. Larger values encourage the AI to leave more room."],
        radius: ["Round area radius", "Sets the radius of a round spacing area. Use a number of steps or one of the game presets shown below."],
        ignore: ["Building types this rule can be close to", "The AI does not keep this building group apart from the listed types; it may place them close together. If absent, the game uses its default spacing behavior."],
        not_ignore: ["Building types to keep apart", "The AI checks spacing against only these types. Other building types are not considered by this rule."],
        terrain: ["Map terrain settings", "These settings help the AI classify land, lakes, and seas; they do not set the space around a building."]
    };
    if (field === "type" && index !== undefined) {
        return {
            label: index === "0" ? "Area shape" : index === "1" ? "Building category" : `Additional type value ${Number(index) + 1}`,
            description: index === "0"
                ? "Choose rectangle for a box-shaped area or circle for a round area."
                : index === "1"
                    ? "Choose the category used by spacing rules. The value explanation updates as you edit it."
                    : `Game setting value: ${value}.`,
            valueDescription: placementValueHelp("type", value)
        };
    }
    if ((field === "size" || field === "yard" || field === "offset") && index !== undefined) {
        const labels = {
            size: ["Width", "Depth"],
            yard: ["Width of extra clearance", "Depth of extra clearance"],
            offset: ["Left/right shift", "Front/back shift"]
        };
        return {
            label: labels[field][Number(index)] || `Value ${Number(index) + 1}`,
            description: field === "offset"
                ? "Measured in placement steps; direction uses the game's standard orientation."
                : "Measured in steps; each step represents 16 units of in-game distance."
        };
    }
    if ((field === "ignore" || field === "not_ignore") && index !== undefined) {
        return {
            label: `Building type ${Number(index) + 1}`,
            description: "The category this spacing rule should treat as described below.",
            valueDescription: placementValueHelp(field, value) || `Game building category "${value}".`
        };
    }
    const info = descriptions[field];
    if (info) {
        return {
            label: info[0],
            description: info[1],
            ...(field === "radius" ? {valueDescription: placementValueHelp(field, value)} : {})
        };
    }
    return {label: parts[parts.length - 1].replace(/_/g, " "), description: "A game-specific setting. Its exact effect may depend on the game version."};
}
function placementView(panel, file, root) {
    const classes = C.at(root, ["building", "class_land"]);
    if (!classes || classes.kind !== "object" || !classes.children.length) return;
    if (!classes.children.some(e => e.key === state.placement)) state.placement = classes.children[0].key;
    const selected = C.at(classes, [state.placement]);
    panel.append(element("h3", "", "Land building spacing"),
        element("p", "muted", "Choose a building group to edit. Group names in the settings file are internal labels; the building list below shows what each group covers. These rules help the AI estimate open space and avoid crowding; they do not change the game's own placement limits."),
        dropdown("Buildings covered by this rule", classes.children.map(e => [e.key, placementGroupLabel(e.key)]), state.placement,
            v => {state.placement = v; render();}));
    const instances = C.at(root, ["building", "instance", state.placement]);
    if (instances && instances.kind === "array") {
        if (instances.children.length) {
            panel.append(element("p", "muted", "This rule applies to these buildings:"));
            const list = element("div", "unit-icon-list");
            for (const entry of instances.children) {
                const id = String(entry.node.value), name = friendlyUnitName(id);
                const item = element("div", "unit-icon-item");
                item.append(unitIcon(id), element("span", "", name ? `${name} (${id})` : id));
                list.append(item);
            }
            panel.append(list);
        } else panel.append(element("p", "muted", "No buildings are listed for this group."));
    } else {
        panel.append(element("p", "muted", "This group has no explicit building list; the group name is an internal label from the settings file."));
    }
    const preview = element("div", "placement-preview");
    function updatePreview() {
        const currentRoot = parse(file), current = C.at(currentRoot, ["building", "class_land", state.placement]);
        const type = C.at(current, ["type", 0]), size = C.at(current, ["size"]), yard = C.at(current, ["yard"]);
        const shape = element("div", type?.value === "circle" ? "placement-shape circle" : "placement-shape",
            type?.value === "circle" ? "Round area" : "Rectangular area");
        const info = element("div");
        const spacing = yard?.children ? yard.children.map(e => e.node.value).join(" × ") : "game default";
        const footprint = size?.children ? size.children.map(e => e.node.value).join(" × ") : "built-in building size";
        info.append(element("h3", "", "Spacing preview"),
            element("p", "muted", "A guide to the configured space, not a map or an exact drawing."),
            element("p", "muted", `Building area: ${footprint} · Extra clearance: ${spacing}`));
        preview.replaceChildren(shape, info);
    }
    updatePreview(); panel.append(preview);
    panel.append(element("p", "muted", "For size, clearance, and radius values, each step represents 16 units of in-game distance. If a value is missing, the game uses its built-in default."));
    if (selected?.children) for (const item of selected.children) {
        panel.append(treeNode(file, item.node, item.key.replace(/_/g, " "), 0, updatePreview, placementInfo));
    }
}
function scriptSettings(panel, path, title, definitions) {
    const section = element("div", "script-settings");
    let resolved;
    try { resolved = S.resolve(state.files, path); }
    catch (error) { section.append(element("h3", "", title), notice(errorText(error), true)); panel.append(section); return; }
    if (!resolved) return;
    const file = resolved.file;
    section.append(element("h3", "", title), element("p", "muted", "These controls edit uniquely recognized expressions in one script file, independently of the selected configuration layer. Other code may affect the result; runtime behavior is not verified."),
        element("p", "muted", baselineLabel(file, `Script source: ${file.path}${resolved.chain.length > 1 ? " (forwarded from " + path + "; other scripts may also use this file)" : ""}`)));
    let matched = 0;
    for (const [label, prefix, suffix, kind, min, max] of definitions) {
        const number = "(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?[fF]?";
        const expression = new RegExp("(" + prefix + ")(" + number + ")(" + suffix + ")", "g");
        function locate() {
            const clean = S.mask(file.text);
            if (/(?:^|\n)\s*#\s*(?!include\b)/.test(clean)) return null;
            const matches = [...clean.matchAll(expression)];
            if (matches.length !== 1) return null;
            const match = matches[0], start = match.index + match[1].length;
            return {start, end: start + match[2].length, literal: file.text.slice(start, start + match[2].length)};
        }
        let current;
        try { current = locate(); } catch (error) { section.append(notice(errorText(error), true)); break; }
        if (!current) continue;
        matched++;
        const row = element("div", "tree-field"), labelNode = element("label", "", label), input = element("input");
        input.type = "number"; input.step = kind === "integer" ? "1" : "any";
        input.min = String(min); if (max !== null) input.max = String(max);
        input.value = Number(current.literal.replace(/[fF]$/, ""));
        input.setAttribute("aria-label", label);
        input.addEventListener("change", () => {
            try {
                current = locate();
                if (!current) throw Error("The script no longer matches this control.");
                const value = Number(input.value);
                if (!input.value.trim() || !Number.isFinite(value) || value < min || (max !== null && value > max) ||
                    (kind === "integer" && !Number.isInteger(value))) throw Error("Enter a value within the indicated range.");
                let encoded = String(value);
                if (/[fF]$/.test(current.literal)) {
                    if (!/[.eE]/.test(encoded)) encoded += ".0";
                    encoded += current.literal.slice(-1);
                }
                const next = file.text.slice(0, current.start) + encoded + file.text.slice(current.end);
                if (path === "script/manager/factory.as") {
                    const ranges = [...S.mask(next).matchAll(/AiRandom\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*\*\s*SECOND/g)];
                    if (ranges.length !== 1 || Number(ranges[0][1]) > Number(ranges[0][2])) {
                        throw Error("The switching lower bound cannot exceed the upper bound.");
                    }
                }
                assign(file, next);
                message("");
            } catch (error) {
                message(errorText(error), true);
                if (current) input.value = Number(current.literal.replace(/[fF]$/, ""));
            }
        });
        row.append(labelNode, input); section.append(row);
    }
    if (!matched) section.append(notice("These script expressions are custom or unsupported. Use All files to inspect them."));
    panel.append(section);
}
function configPage(target, page, selected) {
    const {file} = selected, root = parse(file), panel = element("div", "panel");
    panel.append(layerControl(page, selected));
    panel.append(notice(page === "placement"
        ? "You are editing only the file selected above. The game may also load other files that replace these values."
        : "Editing the selected file only. Conditional layers may override base values; this view does not calculate the effective configuration."));
    if (page !== "units" && page !== "placement") catalogControls(panel);
    if (page === "combat" && file.path.endsWith("/response.json")) responseView(panel, file, root);
    if (page === "economy") economyView(panel, file, root);
    if (page === "units") unitsView(panel, file, root);
    if (page === "defense") defenseView(panel, file, root);
    if (page === "placement") placementView(panel, file, root);
    if (page === "economy") scriptSettings(panel, "script/manager/economy.as", "Economy script thresholds", [
        ["Metal empty: fraction of storage", "aiEconomyMgr\\.isMetalEmpty\\s*=\\s*metal\\.current\\s*<\\s*metal\\.storage\\s*\\*\\s*", "\\s*;", "float", 0, 1],
        ["Metal full: fraction of storage", "aiEconomyMgr\\.isMetalFull\\s*=\\s*metal\\.current\\s*>\\s*metal\\.storage\\s*\\*\\s*", "\\s*;", "float", 0, 1],
        ["Energy full: fraction of storage", "aiEconomyMgr\\.isEnergyFull\\s*=\\s*energy\\.current\\s*>\\s*energy\\.storage\\s*\\*\\s*", "\\s*;", "float", 0, 1]
    ]);
    if (page === "defense") scriptSettings(panel, "script/manager/military.as", "Defense script conditions", [
        ["Defense may start after minutes (or other conditions)", "ai\\.frame\\s*>\\s*", "\\s*\\*\\s*MINUTE", "integer", 0, null],
        ["Defense may start above metal income (or other conditions)", "aiEconomyMgr\\.metal\\.income\\s*>\\s*", "\\s*\\)", "float", 0, null]
    ]);
    panel.append(element("h3", "", page === "placement" ? "More placement settings" : "Explore settings"),
        element("p", "muted", page === "placement"
            ? "The controls above explain land-building spacing. If the file contains water-building rules, they appear below along with settings that help the AI identify lakes and seas."
            : "Open a section to edit its existing values. IDs and unfamiliar fields are shown as written; check your runtime before assuming an effect."));
    const tree = treeNode(file, root, page === "placement" ? "All placement settings" : "All settings", 0, null,
        page === "placement" ? placementInfo : null);
    panel.append(tree, element("div", "source-bar", page === "placement"
        ? "Settings file: building placement rules"
        : "Editing: " + file.path)); target.append(panel);
}
function advanced(target) {
    const panel = element("div", "panel");
    catalogControls(panel);
    if (!state.files.length) { panel.append(notice("No files in this profile.", true)); target.append(panel); return; }
    if (!state.files.some(f => f.path === state.file)) state.file = state.files[0].path;
    panel.append(dropdown("Profile file", state.files.map(f => [f.path, baselineLabel(f, f.path)]), state.file, value => {state.file = value; render();}));
    const file = find(state.file);
    panel.append(notice("Source editing is an escape hatch for custom behavior. JSON syntax is checked before saving; scripts require an engine smoke test."));
    if (file.path.endsWith(".json")) {
        try { panel.append(treeNode(file, parse(file), "Visual configuration tree")); }
        catch (error) { panel.append(notice("Cannot show visual tree: " + errorText(error), true)); }
    }
    const label = element("label", "field", "Source"), textarea = element("textarea");
    textarea.value = file.text; textarea.spellcheck = false; textarea.dataset.source = "true";
    textarea.addEventListener("input", () => assign(file, textarea.value)); label.append(textarea);
    panel.append(label, element("div", "source-bar", "Changes are saved to this profile only. Unknown content is not rewritten."));
    target.append(panel);
}
function matchView(target) {
    const m = state.match, panel = element("div", "panel");
    const info = state.settings;
    panel.append(element("h2", "", "Headless testing"));
    if (info) {
        panel.append(element("p", "muted", `Using ${info.engine} · ${info.bar_root}`));
    } else {
        panel.append(element("p", "muted", "Configure a BAR installation and engine before scanning."),
            button("Installation settings", installationDialog),
            notice("Install saved changes before scanning. A successful smoke test verifies loading and script compilation, not playing strength."));
    }
    const controls = element("div", "row");
    controls.append(button(m.content ? "Refresh installed content" : "Scan installed content", async () => {
        try {
            if (!state.settings) throw Error("Choose and save an installation first.");
            if (dirty().length) throw Error("Save or discard profile changes before testing.");
            m.scanSettingsKey = matchSettingsKey(state.settings);
            m.content = null;
            m.cached = false;
            m.cacheError = "";
            m.cacheKey = "";
            m.snapshot = await api("/api/match/scan", state.settings);
            m.statusError = "";
            render(); pollMatch();
        } catch (error) { message(errorText(error), true); }
    }), button("Cancel operation", async () => {
        try { m.snapshot = await api("/api/match/cancel", {}); render(); }
        catch (error) { message(errorText(error), true); }
    }));
    panel.append(controls);
    const statusBox = element("div", "notice");
    statusBox.id = "match-status";
    statusBox.setAttribute("role", "status");
    statusBox.setAttribute("aria-live", "polite");
    statusBox.textContent = matchStatusText(m.snapshot, m.statusError, m.cacheError);
    const scanProgress = element("progress", "scan-progress");
    scanProgress.id = "scan-progress";
    scanProgress.setAttribute("aria-label", "Scanning installed content");
    scanProgress.hidden = !isScanActive(m.snapshot);
    panel.append(statusBox, scanProgress);
    if (m.cached) panel.append(notice("Showing saved scan results from a previous run. Refresh after changing the selected engine or installing, removing, or updating games, maps, or AIs."));
    if (m.snapshot?.error) panel.append(notice(m.snapshot.error, true));
    if (m.snapshot?.result && m.snapshot.operation === "match") {
        panel.append(element("pre", "", m.snapshot.result.summary || ""));
        if (m.snapshot.logs?.directory) panel.append(element("p", "path", "Logs: " + m.snapshot.logs.directory));
    }
    if (m.content) {
        const content = m.content;
        if (!content.games.length || !content.maps.length || !content.ais.length) {
            panel.append(notice("The scan found no games, maps, or AIs to select.", true));
        } else {
            if (!content.games.includes(m.game)) m.game = content.games[0];
            if (!content.maps.some(map => map.name === m.map)) m.map = content.maps[0].name;
            const choices = content.ais.flatMap(ai => (ai.profiles.length ? ai.profiles : [{key: "", name: "Default"}])
                .map(profile => ({value: JSON.stringify([ai.short_name, ai.version, profile.key]), label: `${ai.name} · ${profile.name}`})));
            if (!choices.some(c => c.value === m.first)) {
                m.first = choices.find(c => JSON.parse(c.value)[0] === state.project)?.value || choices[0].value;
            }
            if (!choices.some(c => c.value === m.second)) m.second = choices[0].value;
            const row = element("div", "row");
            row.append(dropdown("Game", content.games.map(v => [v, v]), m.game, v => { m.game = v; }),
                dropdown("Map", content.maps.map(v => [v.name, v.name]), m.map, v => { m.map = v; }));
            panel.append(row);
            const sides = element("div", "row");
            sides.append(dropdown("Your AI · side 1", choices.map(c => [c.value, c.label]), m.first, v => {m.first = v;}),
                dropdown("Faction", ["Armada", "Cortex", "Legion"].map(v => [v, v]), m.faction1, v => {m.faction1 = v;}),
                dropdown("Opponent · side 2", choices.map(c => [c.value, c.label]), m.second, v => {m.second = v;}),
                dropdown("Opponent faction", ["Armada", "Cortex", "Legion"].map(v => [v, v]), m.faction2, v => {m.faction2 = v;}));
            panel.append(sides);
            const settings = element("div", "row"), minutes = element("input"), minuteLabel = element("label", "field", "Game-time limit (minutes)");
            minutes.type = "number"; minutes.min = "1"; minutes.step = "1"; minutes.value = m.minutes;
            minutes.addEventListener("change", () => { m.minutes = Number(minutes.value); }); minuteLabel.append(minutes);
            const skipLabel = element("label", "field", "Skip smoke test"), skip = element("input");
            skip.type = "checkbox"; skip.checked = m.skip; skip.addEventListener("change", () => {m.skip = skip.checked;}); skipLabel.append(skip);
            settings.append(minuteLabel, skipLabel, button("Start match", async () => {
                try {
                    if (dirty().length) throw Error("Save and install profile changes before testing.");
                    if (!Number.isInteger(m.minutes) || m.minutes <= 0) throw Error("Enter a positive whole-number time limit.");
                    const player = (value, faction, ally) => {
                        const [short_name, version, profile] = JSON.parse(value);
                        return {short_name, version, profile, faction, ally};
                    };
                    m.snapshot = await api("/api/match/start", {...state.settings, game: m.game, map_name: m.map,
                        players: [player(m.first, m.faction1, 0), player(m.second, m.faction2, 1)],
                        minutes: m.minutes, skip_smoke: m.skip});
                    m.statusError = "";
                    render(); pollMatch();
                } catch (error) { message(errorText(error), true); }
            }, "button primary"));
            const activity = element("span", "match-activity");
            activity.id = "match-activity";
            activity.setAttribute("role", "status");
            setMatchActivity(activity, m.snapshot, m.statusError);
            settings.append(activity);
            panel.append(settings);
            if (content.warnings.length) panel.append(notice("Scan warnings: " + content.warnings.join("; "), true));
        }
    }
    target.append(panel);
}
function matchActivity(snapshot, error) {
    if (error) return "Status unavailable — cannot verify engine. Retrying…";
    if (!snapshot || snapshot.operation !== "match") return "";
    if (!["running", "cancelling"].includes(snapshot.state)) {
        return snapshot.state === "failed" ? "Match failed; see status above." : "";
    }
    if (snapshot.phase !== "smoke" && snapshot.phase !== "match") return "Preparing match…";
    const stage = snapshot.phase === "smoke" ? "Smoke test" : "Headless match";
    const seconds = Math.floor(snapshot.phase_elapsed_seconds || 0);
    const elapsed = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
        .map(value => String(value).padStart(2, "0")).join(":");
    const engine = snapshot.engine;
    if (!engine || engine.state === "starting") return `${stage}: starting · ${elapsed}`;
    if (engine.state === "exited") return `${stage}: engine exited (code ${engine.exit_code}); processing result`;
    if (engine.state !== "running") return `${stage}: engine status unavailable`;
    return `${stage}: ${snapshot.state === "cancelling" ? "stopping" : "running"} · ${elapsed}`;
}
function isScanActive(snapshot) {
    return snapshot?.operation === "scan" && ["running", "cancelling"].includes(snapshot.state);
}
function matchStatusText(snapshot, error, cacheError = "") {
    if (error) return "Status unavailable: " + error + ". Retrying…";
    if (cacheError) return "Saved scan results unavailable: " + cacheError + ". Scan installed content to refresh.";
    if (!snapshot) return "No scan or match started.";
    const status = `${snapshot.state}: ${snapshot.message || ""}`;
    if (!isScanActive(snapshot)) return status;
    const seconds = Math.floor(snapshot.elapsed_seconds || 0);
    const elapsed = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    return `${status} · elapsed ${elapsed} · estimated total ~3 min`;
}
function setMatchActivity(node, snapshot, error) {
    node.textContent = matchActivity(snapshot, error);
    node.classList.toggle("error", !!error || snapshot?.state === "failed" ||
        (snapshot?.engine?.state === "exited" && snapshot.engine.exit_code !== 0));
}
function updateMatchStatus() {
    const m = state.match, statusBox = $("match-status"), scanProgress = $("scan-progress"), activity = $("match-activity");
    if (statusBox) statusBox.textContent = matchStatusText(m.snapshot, m.statusError, m.cacheError);
    if (scanProgress) scanProgress.hidden = !isScanActive(m.snapshot);
    if (activity) setMatchActivity(activity, m.snapshot, m.statusError);
}
let matchPoll = null;
async function pollMatch() {
    if (matchPoll) return;
    matchPoll = true;
    try {
        for (;;) {
            let snapshot;
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 5000);
            try {
                snapshot = await api("/api/match/status", undefined, controller.signal);
                state.match.statusError = "";
            } catch (error) {
                state.match.statusError = error.name === "AbortError" ? "request timed out" : errorText(error);
                updateMatchStatus();
                await new Promise(resolve => setTimeout(resolve, 1500));
                continue;
            } finally { clearTimeout(timeout); }
            state.match.snapshot = snapshot;
            updateMatchStatus();
            if (!["running", "cancelling"].includes(snapshot.state)) {
                if (snapshot.operation === "scan") {
                    if (snapshot.state === "completed" &&
                        state.match.scanSettingsKey === matchSettingsKey(state.settings)) {
                        state.match.content = snapshot.result.content;
                        state.match.cached = false;
                        state.match.cacheError = "";
                        state.match.cacheKey = state.match.scanSettingsKey;
                    } else {
                        if (state.match.scanSettingsKey === matchSettingsKey(state.settings)) state.match.cacheKey = "";
                        await loadCachedMatchContent();
                    }
                }
                if (state.page === "match") render();
                break;
            }
            await new Promise(resolve => setTimeout(resolve, 1500));
        }
    } finally { matchPoll = null; }
}
function navigate(page) { state.page = page; render(); }
function render() {
    $("breadcrumb").textContent = pages[state.page][0];
    document.querySelectorAll("#navigation button").forEach(b => {
        b.classList.toggle("active", b.dataset.page === state.page);
        if (b.dataset.page === state.page) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    const target = $("content"); target.replaceChildren(header(state.page));
    if (state.page === "manage") {
        message("");
        manageAIs(target);
        return;
    }
    if (!state.profile) {
        const empty = element("div", "panel");
        empty.append(element("h2", "", "Create your first AI"),
            element("p", "muted", "Start with a copy of the BARbarIAn hard profile from an installed game. The original game files stay unchanged."),
            button("New AI from BAR", () => createProject(), "button primary"));
        target.append(empty); return;
    }
    try {
        if (state.page === "overview") overview(target);
        else if (state.page === "opening") opening(target);
        else if (state.page === "advanced") advanced(target);
        else if (state.page === "match") matchView(target);
        else {
            const selected = chooseFile(state.page);
            if (!selected) target.append(notice("No corresponding configuration file exists in this profile.", true));
            else if (state.page === "production") production(target, selected);
            else configPage(target, state.page, selected);
        }
    } catch (error) {
        target.append(notice(state.page === "match"
            ? "Cannot display Test match: " + errorText(error)
            : "Cannot display this file: " + errorText(error) + ". Use All files to inspect the source.", true));
    }
}
async function save() {
    if (state.saving) return;
    try {
        const warnings = [];
        for (const file of dirty()) {
            if (!file.path.endsWith(".json")) continue;
            const issues = validateConfig(file);
            let originalIssues = [];
            try { originalIssues = validateConfig({path: file.path, text: file.original}); } catch (_) { /* Original invalid syntax is not a new edit. */ }
            const introduced = issues.filter(issue => !originalIssues.includes(issue));
            if (introduced.length) throw Error(introduced.join("\n"));
            warnings.push(...issues);
        }
        const submitted = dirty().map(f => ({path: f.path, text: f.text, sha256: f.sha256}));
        state.saving = true; status();
        const result = await api("/api/save", {project: state.project, profile: state.profile, files: submitted});
        for (const saved of result.files) {
            const file = find(saved.path);
            const snapshot = submitted.find(f => f.path === saved.path);
            if (file && snapshot) { file.original = snapshot.text; file.sha256 = saved.sha256; }
        }
        await refreshBaseline();
        render();
        message("Profile changes saved. Install the AI before testing in BAR." +
            (warnings.length ? "\nExisting configuration warnings:\n" + warnings.join("\n") : ""), warnings.length > 0);
    } catch (error) { message("Save failed: " + errorText(error), true); }
    finally { state.saving = false; status(); }
}
function validateConfig(file) {
    const root = parse(file), issues = [];
    const factories = C.at(root, ["factory"]);
    if (factories?.kind === "object") {
        for (const fac of factories.children) {
            const units = C.at(fac.node, ["unit"]);
            if (!units || units.kind !== "array") continue;
            for (const situation of ["land", "water", "air"]) {
                const group = C.at(fac.node, [situation]);
                if (group?.kind !== "object") continue;
                for (const tier of group.children) {
                    if (/^tier\d+$/.test(tier.key) && tier.node.kind === "array" && tier.node.children.length !== units.children.length) {
                        issues.push(`${file.path}: ${fac.key} ${situation} ${tier.key} has ${tier.node.children.length} weights for ${units.children.length} units.`);
                    }
                }
            }
        }
    }
    const response = C.at(root, ["response"]);
    if (response?.kind === "object") {
        for (const role of response.children) {
            const lists = ["vs", "ratio", "importance"].map(key => C.at(role.node, [key]));
            if (lists.every(list => list?.kind === "array") && lists.some(list => list.children.length !== lists[0].children.length)) {
                issues.push(`${file.path}: ${role.key} response list lengths are ${lists.map(list => list.children.length).join(", ")}.`);
            }
        }
    }
    for (const surface of ["land", "water"]) {
        const generators = C.at(root, ["economy", "energy", surface]);
        if (generators?.kind !== "object") continue;
        for (const generator of generators.children) {
            const values = generator.node.children;
            if (generator.node.kind === "array" && values?.length >= 2 &&
                values[0].node.kind === "number" && values[1].node.kind === "number" &&
                (!Number.isInteger(values[0].node.value) || !Number.isInteger(values[1].node.value) || values[1].node.value < values[0].node.value)) {
                issues.push(`${file.path}: ${generator.key} generator count bounds ${values[0].node.value}, ${values[1].node.value} must be whole numbers with upper ≥ lower.`);
            }
        }
    }
    return issues;
}
function review() {
    const box = element("div");
    box.append(button("Discard all unsaved changes", () => {
        if (!confirm("Discard every unsaved change in this profile?")) return;
        for (const file of dirty()) assign(file, file.original);
        $("dialog").close(); message("Unsaved changes discarded."); render();
    }));
    for (const file of dirty()) {
        const details = element("details"), before = file.original.split("\n"), after = file.text.split("\n");
        let start = 0, endBefore = before.length, endAfter = after.length;
        while (start < endBefore && start < endAfter && before[start] === after[start]) start++;
        while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) { endBefore--; endAfter--; }
        details.append(element("summary", "", file.path),
            button("Restore this file", () => {
                if (!confirm(`Discard unsaved changes to ${file.path}?`)) return;
                assign(file, file.original); $("dialog").close(); render();
            }),
            element("pre", "", `From line ${start + 1}\n\nBEFORE\n${before.slice(start, endBefore).join("\n")}\n\nAFTER\n${after.slice(start, endAfter).join("\n")}`));
        box.append(details);
    }
    showDialog("Review changes", box);
}
async function projectHistory() {
    const project = state.project, profile = state.profile;
    const box = element("div"), feedback = element("p", "muted"), list = element("div");
    const [label, input] = textInput("Checkpoint description", "", "e.g. Adjust swarm production");
    const diffView = element("div");
    let diffRequest = 0;
    async function showDiff(revisions) {
        const request = ++diffRequest;
        diffView.replaceChildren(element("p", "muted", "Loading checkpoint diff…"));
        try {
            const {paths, patch} = await api("/api/history/diff", {project, ...revisions});
            if (request !== diffRequest) return;
            const files = element("ul");
            for (const path of paths) files.append(element("li", "", path));
            diffView.replaceChildren(element("h3", "", "Changed files"),
                paths.length ? files : element("p", "muted", "No differences."),
                element("pre", "checkpoint-diff", patch));
        } catch (error) {
            if (request === diffRequest) diffView.replaceChildren(notice(errorText(error), true));
        }
    }
    box.append(element("p", "muted", "History covers the entire AI project, including every profile. Save is separate from creating a checkpoint. History is local and is not a backup."),
        label, feedback, list, diffView);
    showDialog("Project history", box);
    function draw(checkpoints) {
        list.replaceChildren(element("h3", "", "Checkpoints"));
        const choices = checkpoints.map(checkpoint => [checkpoint.id,
            `${checkpoint.description} · ${checkpoint.id.slice(0, 12)}`]);
        let base = checkpoints.at(-1)?.id;
        let commit = "";
        const compare = element("div", "row");
        compare.append(dropdown("From checkpoint", choices, base, value => { base = value; }),
            dropdown("To", [["", "Current saved files"], ...choices], commit, value => { commit = value; }),
            button("Compare", () => showDiff({base, ...(commit ? {commit} : {})})));
        list.append(compare);
        for (const checkpoint of checkpoints) {
            const row = element("div", "chain-card"), details = element("div");
            details.append(element("strong", "", checkpoint.description),
                element("p", "muted", `${checkpoint.date} · ${checkpoint.id.slice(0, 12)}`));
            const restore = button("Restore this checkpoint", async () => {
                if (state.saving || state.project !== project) return;
                if (dirty().length) { feedback.textContent = "Save or discard this profile's unsaved edits before restoring."; return; }
                if (!confirm(`Restore "${checkpoint.description}" across the entire AI project? The project must have no uncheckpointed changes. This changes files but does not erase history.`)) return;
                state.saving = true; status(); restore.disabled = true;
                let restored = false;
                try {
                    await api("/api/history/restore", {project, commit: checkpoint.id, confirm: true});
                    restored = true;
                    await refreshState(project, profile);
                    await loadProfile();
                    $("dialog").close();
                    message("Checkpoint restored as project file changes. Create a checkpoint to record the restore.");
                } catch (error) {
                    feedback.textContent = (restored ? "Restore succeeded, but reloading failed: " : "Restore failed: ") + errorText(error);
                } finally { state.saving = false; status(); restore.disabled = false; }
            });
            row.append(details, button("View diff", () => showDiff({commit: checkpoint.id})), restore); list.append(row);
        }
    }
    const create = button("Create checkpoint", async () => {
        if (state.saving || state.project !== project) return;
        if (dirty().length) { feedback.textContent = "Save or discard this profile's unsaved edits before creating a checkpoint."; return; }
        state.saving = true; status(); create.disabled = true;
        try {
            const result = await api("/api/history/checkpoint", {project, description: input.value.trim()});
            input.value = ""; draw(result.checkpoints); feedback.textContent = "Checkpoint created.";
        } catch (error) { feedback.textContent = "Checkpoint failed: " + errorText(error); }
        finally { state.saving = false; status(); create.disabled = false; }
    }, "button primary");
    box.insertBefore(create, feedback);
    try {
        const result = await api("/api/history", {project});
        draw(result.checkpoints);
    } catch (error) { feedback.textContent = "History unavailable: " + errorText(error); }
}
function cloneProfile() {
    if (state.saving) { message("Wait for the save to finish.", true); return; }
    const box = element("div"), [label, input] = textInput("New profile name", state.profile + " copy");
    box.append(element("p", "muted", "Create an independent copy of the saved profile. Unsaved changes are not included."), label,
        button("Duplicate profile", async () => {
            try {
                if (!confirmDiscard()) return;
                const result = await api("/api/clone", {project: state.project, profile: state.profile, name: input.value});
                $("dialog").close(); await refreshState(state.project, result.profile); await loadProfile();
                message("Profile duplicated.");
            } catch (error) { message(errorText(error), true); }
        }, "button primary"));
    showDialog("Duplicate profile", box);
}
function cloneProject() {
    if (state.saving) { message("Wait for the save to finish.", true); return; }
    const box = element("div"), [label, input] = textInput("New AI name", "");
    box.append(element("p", "muted", "Copy this project's saved profiles into a new independent AI. The original remains unchanged."), label,
        button("Create AI", async () => {
            try {
                if (!confirmDiscard()) return;
                const result = await api("/api/clone-project", {project: state.project, name: input.value});
                $("dialog").close(); await refreshState(result.project, state.profile); await loadProfile();
                message("AI created from the selected project." + (result.history_error ? " Project history unavailable: " + result.history_error : ""), !!result.history_error);
            } catch (error) { message(errorText(error), true); }
        }, "button primary"));
    showDialog("Create an AI", box);
}
function createProject(project = "") {
    if (state.saving) { message("Wait for the save to finish.", true); return; }
    const box = element("div"), [nameLabel, name] = textInput(project ? "Profile name" : "AI name");
    const [rootLabel, root] = textInput("BAR installation folder", state.settings?.bar_root || state.defaultBarRoot, "C:\\Games\\Beyond-All-Reason");
    const sourceBox = element("div"), feedback = element("p", "muted");
    let source = null;
    box.append(element("p", "muted", "Select an installed game version to copy its working hard profile. The source and your existing AIs are not changed."),
        nameLabel, rootLabel, button("Find installed game versions", async () => {
            try {
                const result = await api("/api/sources", {bar_root: root.value});
                source = element("select");
                const choices = [["", "Choose a game version"], ...result.sources.map(item => [item.label, item.label])];
                selectOptions(source, choices, result.suggested || "");
                const label = element("label", "field", "Source game version"); label.append(source);
                sourceBox.replaceChildren(label);
                feedback.textContent = result.suggested ? "Suggested from the last game log; verify it is the version you want." :
                    result.sources.length ? "Choose the game version to copy." : "No installed game sources were found.";
            } catch (error) { source = null; sourceBox.replaceChildren(); feedback.textContent = errorText(error); }
        }), sourceBox, feedback, button(project ? "Create profile" : "Create AI", async event => {
            if (state.saving) return;
            const submit = event.currentTarget;
            let started = false;
            try {
                if (!source || !source.value) throw Error("Find and choose an installed game version first.");
                if (!confirmDiscard()) return;
                state.saving = started = true; status(); submit.disabled = true;
                const input = {bar_root: root.value, source: source.value, name: name.value};
                const result = project
                    ? await api("/api/project/manage", {...input, project, action: "create_profile"})
                    : await api("/api/create-project", input);
                await refreshState(result.project, result.profile || "hard"); await loadProfile(); $("dialog").close();
                const history = result.history_error ? "\nProject history unavailable: " + result.history_error : "";
                message(`${project ? "Profile" : "AI"} created from the selected game version.` + history, !!history);
            } catch (error) { feedback.textContent = errorText(error); }
            finally {
                if (started) { state.saving = false; status(); submit.disabled = false; }
            }
        }, "button primary"));
    showDialog(project ? "New profile from BAR" : "New AI from BAR", box);
}
async function projectArchiveRequest(url, file) {
    const response = await fetch(url, {
        headers: {"X-Workshop-Token": window.WORKSHOP_TOKEN},
        ...(file ? {method: "POST", body: file} : {})
    });
    if (!response.ok) {
        const detail = await response.json();
        throw Error(detail.error || `Request failed (${response.status})`);
    }
    return response;
}
function manageProject() {
    const project = selectedProject(), profile = project?.profiles.find(entry => entry.key === state.profile);
    const box = element("div"), feedback = element("p", "muted"), archive = element("input");
    archive.type = "file"; archive.accept = ".zip,application/zip";
    const archiveLabel = element("label", "field", "Project ZIP");
    archiveLabel.append(archive);

    async function mutate(event, submit, success) {
        const control = event.currentTarget;
        if (state.saving || !confirmDiscard()) return;
        state.saving = true; status(); control.disabled = true;
        try {
            const result = await submit();
            await refreshState(result.project, result.profile ?? state.profile);
            await loadProfile(); $("dialog").close(); message(success);
        } catch (error) { feedback.textContent = errorText(error); }
        finally { state.saving = false; status(); control.disabled = false; }
    }
    const change = (event, action, fields, success) => mutate(event, () => api("/api/project/manage", {
        project: project.key, profile: profile?.key, action, ...fields
    }), success);
    if (project) {
        const [projectLabel, projectName] = textInput("Project display name", project.name);
        box.append(element("h3", "", "Project"), projectLabel,
            button("Rename project", event => change(event, "rename_project", {name: projectName.value}, "Project renamed.")),
            button("Export project ZIP", async event => {
                const control = event.currentTarget;
                control.disabled = true;
                try {
                    const response = await projectArchiveRequest(`/api/project/export?project=${encodeURIComponent(project.key)}`);
                    const link = element("a");
                    link.href = URL.createObjectURL(await response.blob()); link.download = project.key + ".zip";
                    link.click();
                    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
                    feedback.textContent = "Project ZIP exported.";
                } catch (error) { feedback.textContent = errorText(error); }
                finally { control.disabled = false; }
            }),
            button("Delete project", event => {
                if (confirm(`Permanently delete ${project.name}, its profiles, and its checkpoint history?`)) {
                    change(event, "delete_project", {confirm: true}, "Project deleted.");
                }
            }));
        box.append(element("h3", "", "Profiles"), button("New profile from BAR", () => createProject(project.key)));
        if (profile) {
            const isDefault = project.default_profile === profile.key;
            const [profileLabel, profileName] = textInput("Profile display name", profile.name);
            const setDefault = button("Make default", event => change(event, "set_default", {}, "Default profile updated. Install the AI to apply its new default in BAR."));
            const remove = button("Delete profile", event => {
                if (confirm(`Delete the ${profile.name} profile?`)) change(event, "delete_profile", {confirm: true}, "Profile deleted.");
            });
            setDefault.disabled = isDefault;
            remove.disabled = isDefault || project.profiles.length === 1;
            box.append(profileLabel,
                button("Rename profile", event => change(event, "rename_profile", {name: profileName.value}, "Profile renamed.")),
                setDefault, remove);
            if (isDefault) box.append(element("p", "muted", "This is the default profile. Select a different default before deleting it."));
        }
    }
    box.append(element("h3", "", "Import a project"), archiveLabel, button("Import ZIP", event => {
        if (!archive.files.length) { feedback.textContent = "Choose a project ZIP."; return; }
        mutate(event, async () => (await projectArchiveRequest("/api/project/import", archive.files[0])).json(),
            "Project imported with a fresh baseline checkpoint.");
    }), feedback);
    showDialog("Manage projects and profiles", box);
}
function manageAIs(target) {
    const setup = element("div", "panel"), [rootLabel, root] = textInput(
        "BAR installation folder", state.settings?.bar_root || state.defaultBarRoot, "C:\\Games\\Beyond-All-Reason"
    );
    const currentSelection = element("p", "muted");
    currentSelection.append(document.createTextNode("Current selection: "));
    currentSelection.append(element("span", state.settings ? "" : "selection-none", state.settings
        ? `${state.settings.engine} · ${state.settings.bar_root}`
        : "None"));
    const engine = element("select"), engineLabel = element("label", "field", "Engine");
    const feedback = element("p", "muted"), saveButton = button("Save installation selection", saveSelection);
    let inspectedRoot = null, inspectTimer = null, editing = !state.settings;
    engine.disabled = true;
    selectOptions(engine, [["", "Enter a BAR installation folder"]], "");
    engineLabel.append(engine);

    async function inspectInstallation() {
        const requestedRoot = root.value.trim();
        inspectedRoot = null;
        engine.disabled = true;
        saveButton.disabled = true;
        if (!requestedRoot) {
            selectOptions(engine, [["", "Enter a BAR installation folder"]], "");
            feedback.textContent = "";
            return;
        }
        selectOptions(engine, [["", "Loading engines..."]], "");
        feedback.textContent = "Finding installed engines...";
        try {
            const info = await api("/api/inspect", {bar_root: requestedRoot});
            if (root.value.trim() !== requestedRoot) return;
            const choices = info.engines.map(value => [value, value]);
            if (!choices.length) throw Error("No engines were found in this installation.");
            selectOptions(engine, [["", "Choose an engine"], ...choices],
                state.settings?.bar_root === info.root && info.engines.includes(state.settings.engine)
                    ? state.settings.engine
                    : info.active_engine || info.engines[info.engines.length - 1]);
            inspectedRoot = requestedRoot;
            engine.disabled = false;
            saveButton.disabled = !engine.value;
            feedback.textContent = info.active_engine_message || "Installation found.";
        } catch (error) {
            if (root.value.trim() !== requestedRoot) return;
            selectOptions(engine, [["", "Engines unavailable"]], "");
            feedback.textContent = errorText(error);
        }
    }

    async function saveSelection() {
        try {
            if (!engine.value || inspectedRoot !== root.value.trim()) {
                throw Error("Choose a BAR installation and engine first.");
            }
            saveButton.disabled = true;
            const saved = await api("/api/settings", {bar_root: root.value.trim(), engine: engine.value});
            setSettings(saved);
            await loadIconStatus();
            await loadCachedMatchContent();
            render();
        } catch (error) {
            feedback.textContent = errorText(error);
            saveButton.disabled = !engine.value || inspectedRoot !== root.value.trim();
        }
    }

    function renderSetup() {
        setup.replaceChildren(element("h2", "", "1. BAR installation"), currentSelection);
        if (state.settings && !editing) {
            setup.append(button("Change BAR installation", () => {
                editing = true;
                renderSetup();
                inspectInstallation();
            }));
            return;
        }
        setup.append(rootLabel, engineLabel, feedback, saveButton);
    }

    root.addEventListener("input", () => {
        inspectedRoot = null;
        engine.disabled = true;
        saveButton.disabled = true;
        clearTimeout(inspectTimer);
        inspectTimer = setTimeout(inspectInstallation, 350);
    });
    engine.addEventListener("change", () => {
        saveButton.disabled = !engine.value || inspectedRoot !== root.value.trim();
    });
    renderSetup();
    if (editing) inspectInstallation();

    const actions = element("div", "panel");
    const project = selectedProject();
    const selectedEngine = state.settings;
    const ready = !!(project && selectedEngine && !state.saving);
    actions.append(element("h2", "", "2. Manage the selected AI"),
        element("p", "muted", project
            ? `Target AI: ${project.name} (${project.key})`
            : "Create or select an AI project before installing or removing it."),
        element("p", "muted", selectedEngine
            ? `Target engine: ${selectedEngine.engine} · ${selectedEngine.bar_root}`
            : "Save an installation selection above to enable these actions."),
        notice("Install deploys the selected project's saved files. Remove only affects an installation previously managed by this editor. Close BAR before either action."));
    const installButton = button("Install / update selected AI", () => manageSelectedAI(
        false, deploymentStatus, installButton, removeButton
    ), "button primary");
    const removeButton = button("Remove selected AI from BAR", () => manageSelectedAI(
        true, deploymentStatus, installButton, removeButton
    ), "action");
    installButton.disabled = !ready;
    removeButton.disabled = !ready;
    const controls = element("div", "row");
    controls.append(installButton, removeButton);
    const deploymentStatus = element("p", "deployment-status");
    deploymentStatus.hidden = true;
    deploymentStatus.setAttribute("role", "status");
    deploymentStatus.setAttribute("aria-live", "polite");
    actions.append(controls, deploymentStatus);
    target.append(setup, actions);
}
async function manageSelectedAI(remove, feedback, installButton, removeButton) {
    const wasInstallDisabled = installButton.disabled, wasRemoveDisabled = removeButton.disabled;
    const setFeedback = (text, error = false) => {
        feedback.textContent = text;
        feedback.hidden = !text;
        feedback.className = "deployment-status" + (error ? " error" : "");
    };
    try {
        if (state.saving) throw Error("Wait for the save to finish.");
        if (!state.project) throw Error("Select an AI project first.");
        if (!state.settings) throw Error("Inspect and save a BAR installation selection first.");
        if (!remove && dirty().length) throw Error("Save or discard profile changes before installing.");
        const action = remove ? "remove" : "install";
        const title = selectedProject()?.name || state.project;
        setFeedback(`${remove ? "Removing" : "Installing"} ${title}...`);
        installButton.disabled = true;
        removeButton.disabled = true;
        if (remove && !confirm(`Remove ${title} from ${state.settings.engine}?\n\nOnly this editor-managed installation is removed. BAR must be restarted to refresh its AI list.`)) {
            setFeedback("Removal cancelled. No changes were made.");
            return;
        }
        const confirmations = [];
        let result;
        for (;;) {
            result = await api(`/api/${action}`, {
                project: state.project,
                bar_root: state.settings.bar_root,
                engine: state.settings.engine,
                confirmations
            });
            if (!result.confirmation) break;
            if (confirmations.includes(result.confirmation)) throw Error("The requested confirmation was not accepted.");
            setFeedback("Waiting for confirmation...");
            if (!confirm(result.confirmation)) {
                setFeedback(`${remove ? "Removal" : "Installation"} cancelled. No changes were made.`);
                return;
            }
            confirmations.push(result.confirmation);
        }
        const success = remove
            ? `${title} removed successfully. Restart BAR to refresh the AI list.`
            : result.changed
                ? `${title} installed or updated successfully. Restart BAR to refresh the AI list.`
                : `${title} is already installed and up to date; no changes were needed.`;
        setFeedback(success);
    } catch (error) {
        setFeedback(`${remove ? "Removal" : "Installation"} failed: ${errorText(error)}`, true);
    } finally {
        installButton.disabled = wasInstallDisabled;
        removeButton.disabled = wasRemoveDisabled;
    }
}
$("dialog-close").addEventListener("click", () => $("dialog").close());
$("new-project").addEventListener("click", () => createProject());
$("manage-project").addEventListener("click", manageProject);
$("clone").addEventListener("click", cloneProfile);
$("reload").addEventListener("click", async () => {
    if (!confirmDiscard()) return;
    try { await loadProfile(); message("Profile reloaded from disk."); }
    catch (error) { message("Reload failed: " + errorText(error), true); }
});
$("save").addEventListener("click", save);
$("undo").addEventListener("click", () => {
    const previous = state.history.pop();
    const file = previous && find(previous.path);
    if (file) { assign(file, previous.before, false); message("Last edit undone."); render(); }
    status();
});
$("review").addEventListener("click", review);
$("project-history").addEventListener("click", projectHistory);
$("project").addEventListener("change", async event => {
    if (!confirmDiscard()) { event.target.value = state.project; return; }
    try { await refreshState(event.target.value, ""); await loadProfile(); message(""); }
    catch (error) { message(errorText(error), true); }
});
$("profile").addEventListener("change", async event => {
    if (!confirmDiscard()) { event.target.value = state.profile; return; }
    state.profile = event.target.value;
    try { await loadProfile(); message(""); } catch (error) { message(errorText(error), true); }
});
document.querySelectorAll("#navigation button").forEach(b => b.addEventListener("click", () => navigate(b.dataset.page)));
window.addEventListener("beforeunload", event => { if (dirty().length) { event.preventDefault(); event.returnValue = ""; } });
const clientId = crypto.randomUUID();
let sessionConnectionError = false;
async function heartbeat() {
    try {
        await api("/api/client/heartbeat", {client_id: clientId});
        if (sessionConnectionError) {
            sessionConnectionError = false;
            message("Connection to the local server restored.");
        }
    } catch (error) {
        if (!sessionConnectionError) message("Local server connection failed: " + errorText(error), true);
        sessionConnectionError = true;
    }
}
window.addEventListener("pageshow", event => { if (event.persisted) heartbeat(); });
window.addEventListener("pagehide", event => {
    if (event.persisted) return;
    fetch("/api/client/close", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-Workshop-Token": window.WORKSHOP_TOKEN},
        body: JSON.stringify({client_id: clientId}),
        keepalive: true
    }).catch(error => console.error("Could not notify the local server that the page closed.", error));
});
heartbeat();
setInterval(heartbeat, 30_000);
(async () => {
    try {
        try { await loadUnitNames(); }
        catch (error) { message("Could not load bundled unit names: " + errorText(error), true); }
        await refreshState();
        try { setCatalog((await api("/api/catalog/latest")).catalog); }
        catch (error) { message("Could not load saved unit catalog: " + errorText(error), true); }
        await loadProfile();
    }
    catch (error) { message("Could not load workshop: " + errorText(error), true); }
})();
