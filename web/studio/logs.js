"use strict";

// Match log viewer. The first half is pure data handling (parsing, severity, filtering, search) and is unit
// tested in tests/studio_logs.test.js; the second half builds the DOM. Server pages come from
// /api/match/logs. Large artifacts can be browsed in bounded sections.
window.StudioLogs = (() => {
    const STAMP = /^\[t=(\d+):(\d{2}):(\d{2})\.(\d+)\]\s?(?:\[f=(-?\d+)\]\s?)?/;
    const ERROR_PREFIX = /^(?:error|fatal)\b/i;
    const AI_ERROR = new RegExp([
        String.raw`\(\d+,\s*\d+\)\s*:\s*ERR\b`, "fix compilation errors", "script: unable to add section",
        String.raw`\b(?:angel\s*script|script)\b.*\b(?:error|exception|failed|missing)\b`,
        String.raw`\b(?:exception|runtime error)\b`,
    ].join("|"), "i");
    const AI_WARN = /\(\d+,\s*\d+\)\s*:\s*WARN\b|(?:angel\s*script|script)\b.*\bwarn\w*\b/i;
    const MAX_PAGES = 24;
    const MAX_ROWS = 13000;
    const PREFS_KEY = "bar-studio.logviewer";

    const BLOCK_START = /^=+\s*<([^/>][^>]*)>\s*=+\s*$/;
    const BLOCK_END = /^=+\s*<\/([^>]+)>\s*=+\s*$/;

    function parseLine(text) {
        if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
        const match = STAMP.exec(text);
        if (!match) return {stamped: false, time: "", frame: null, body: text};
        return {
            stamped: true,
            time: `${match[1].padStart(2, "0")}:${match[2]}:${match[3]}.${match[4].slice(0, 3).padEnd(3, "0")}`,
            frame: match[5] === undefined ? null : Number(match[5]),
            body: text.slice(match[0].length),
        };
    }

    function severityOf(category, body) {
        if (category === "engine_error") return "error";
        if (category === "engine_warning") return "warn";
        if (category === "ai_script") {
            if (ERROR_PREFIX.test(body) || AI_ERROR.test(body)) return "error";
            if (AI_WARN.test(body)) return "warn";
        }
        return "none";
    }

    // Collapses numbers and addresses so repeated messages that differ only in values share a key.
    function normalizeKey(body) {
        return body.replace(/0x[0-9a-f]+/gi, "0x#").replace(/\d+/g, "#").replace(/\s+/g, " ").trim().slice(0, 300);
    }

    // Stitches server pages into lines; a page that does not end in a newline continues on the next page.
    function joinPages(pages) {
        const rows = [];
        let carry = false;
        for (const page of pages) {
            const lines = page.lines || [];
            lines.forEach((line, index) => {
                if (index === 0 && carry && rows.length) {
                    const last = rows[rows.length - 1];
                    last.text += line.text;
                    if (last.category === "other") last.category = line.category;
                } else rows.push({text: line.text, category: line.category});
            });
            carry = lines.length > 0 && page.ends_with_newline === false && page.next_offset != null;
        }
        return rows;
    }

    async function readSection(load, start, progress, maxPages = MAX_PAGES) {
        const pages = [];
        let offset = start.offset, completePages = 0, page;
        for (let count = 0; count < maxPages; count++) {
            page = await load(offset);
            if (page.missing) return {rows: [], next: null, missing: true};
            pages.push(page);
            if (page.ends_with_newline) completePages = pages.length;
            offset = page.next_offset;
            if (offset == null) break;
            progress(pages.length);
        }
        // Begin the next section at a line boundary when the unfinished line can be reread there.
        if (offset != null && !page.ends_with_newline && completePages) {
            pages.length = completePages;
            page = pages[completePages - 1];
            offset = page.next_offset;
        }
        const rows = joinPages(pages);
        const continued = rows.length > 0 && !page.ends_with_newline;
        const next = offset == null ? null : {offset, firstLine: start.firstLine + rows.length - Number(continued), continued};
        return {rows, next, endsWithNewline: page.ends_with_newline};
    }

    // Lines without a timestamp continue the previous entry and inherit its category and severity.
    function buildEntries(rows, plain = false, firstLine = 1) {
        const entries = [];
        let parent = -1;
        rows.forEach((row, index) => {
            const parsed = plain ? {stamped: true, time: "", frame: null, body: row.text} : parseLine(row.text);
            const stamped = parsed.stamped || parent < 0;
            let category = row.category || "other";
            let severity = severityOf(category, parsed.body);
            if (stamped) parent = index;
            else {
                if (category === "other") category = entries[parent].category;
                if (severity === "none") severity = entries[parent].severity;
            }
            entries.push({
                n: firstLine + index, raw: row.text, category, severity, time: parsed.time, frame: parsed.frame,
                body: parsed.body, continuation: !stamped, parent, key: stamped ? normalizeKey(parsed.body) : "",
                foldCount: 0, inFold: -1,
            });
        });
        if (!plain) markFoldableBlocks(entries);
        return entries;
    }

    // Marks "==== <Name> ====" ... "==== </Name> ====" banner pairs so their interior can be folded.
    function markFoldableBlocks(entries) {
        let open = null;
        entries.forEach((entry, index) => {
            if (entry.continuation) return;
            const end = BLOCK_END.exec(entry.body.trim());
            if (open && end && end[1].trim() === open.name) {
                entries[open.index].foldCount = index - open.index;
                for (let i = open.index + 1; i <= index; i++) entries[i].inFold = open.index;
                open = null;
                return;
            }
            const start = BLOCK_START.exec(entry.body.trim());
            if (start) open = {name: start[1].trim(), index};
        });
    }

    function matchesFilter(entry, filter) {
        switch (filter) {
            case "error": return entry.severity === "error";
            case "warn": return entry.severity === "warn";
            case "ai": return entry.category === "ai_script";
            case "other": return entry.category === "other";
            default: return true;
        }
    }

    function countEntries(entries) {
        const counts = {all: entries.length, error: 0, warn: 0, ai: 0, other: 0};
        for (const entry of entries) {
            if (entry.severity === "error") counts.error++;
            else if (entry.severity === "warn") counts.warn++;
            if (entry.category === "ai_script") counts.ai++;
            if (entry.category === "other") counts.other++;
        }
        return counts;
    }

    function searchPattern(term) {
        const text = String(term || "").trim();
        return text ? new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i") : null;
    }

    function splitHits(text, pattern) {
        if (!pattern) return [{text, hit: false}];
        const re = new RegExp(pattern.source, "gi"), parts = [];
        let last = 0, match;
        while ((match = re.exec(text))) {
            if (!match[0]) { re.lastIndex++; continue; }
            if (match.index > last) parts.push({text: text.slice(last, match.index), hit: false});
            parts.push({text: match[0], hit: true});
            last = match.index + match[0].length;
        }
        if (last < text.length || !parts.length) parts.push({text: text.slice(last), hit: false});
        return parts;
    }

    // Decides which lines to show. Rows are {entry, context, repeat} or {gap: true, hidden}.
    function computeView(entries, options = {}) {
        const {filter = "all", pattern = null, context = 0, collapse = false,
            fold = false, expanded = new Set(), maxRows = MAX_ROWS} = options;
        const count = entries.length, filtering = filter !== "all" || Boolean(pattern);
        const matched = entries.map(entry => matchesFilter(entry, filter) &&
            (!pattern || pattern.test(entry.raw)));
        const included = matched.slice(), asContext = new Array(count).fill(false);
        if (context > 0 && filtering) {
            for (let i = 0; i < count; i++) {
                if (!matched[i]) continue;
                for (let j = Math.max(0, i - context); j <= Math.min(count - 1, i + context); j++) {
                    if (!included[j]) { included[j] = true; asContext[j] = true; }
                }
            }
        }
        const totals = new Map(), first = new Map(), hidden = new Array(count).fill(false);
        if (collapse && !pattern) {
            entries.forEach((entry, i) => {
                if (matched[i] && !entry.continuation && entry.key) totals.set(entry.key, (totals.get(entry.key) || 0) + 1);
            });
            entries.forEach((entry, i) => {
                if (!matched[i] || entry.continuation || !entry.key) return;
                if (first.has(entry.key)) hidden[i] = true; else first.set(entry.key, i);
            });
            entries.forEach((entry, i) => { if (entry.continuation && hidden[entry.parent]) hidden[i] = true; });
        }
        // Banner blocks stay folded unless expanded, but never hide lines the user is filtering or searching for.
        const folding = fold && !filtering;
        if (folding) entries.forEach((entry, i) => { if (entry.inFold >= 0 && !expanded.has(entry.inFold)) hidden[i] = true; });
        const showGaps = context > 0 && filtering, rows = [];
        let previous = -1, shown = 0, truncated = false;
        for (let i = 0; i < count; i++) {
            if (!included[i] || hidden[i]) continue;
            if (shown >= maxRows) { truncated = true; break; }
            if (showGaps && i - previous > 1) rows.push({gap: true, hidden: i - previous - 1});
            const entry = entries[i], total = first.get(entry.key) === i ? totals.get(entry.key) : 0;
            rows.push({entry, context: asContext[i], repeat: total > 1 ? total : 0,
                fold: folding && entry.foldCount ? {index: i, count: entry.foldCount, open: expanded.has(i)} : null});
            previous = i;
            shown++;
        }
        if (showGaps && !truncated && rows.length && previous < count - 1) rows.push({gap: true, hidden: count - 1 - previous});
        return {rows, shown, truncated, matched: matched.filter(Boolean).length};
    }

    // ---------------------------------------------------------------- DOM

    function el(tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    }
    const logTips = {
        "↑": "Go to the previous search hit, or search the previous section if needed. Shift+Enter does the same in the search box.",
        "↓": "Go to the next search hit, or search the next section if needed. Enter does the same in the search box.",
        All: "Show every message type. Search still highlights matching text.",
        Errors: "Show messages marked as errors. Add context lines to see what happened nearby.",
        Warnings: "Show messages marked as warnings. Some warnings are expected during startup.",
        "AI / AngelScript": "Show messages classified as AI or script output, including messages about the AI's rules.",
        Other: "Show messages outside the named error, warning, and AI groups.",
        "▲ Error": "Jump to the previous visible error in this section. The current filter controls which errors are visible.",
        "▼ Error": "Jump to the next visible error in this section. The current filter controls which errors are visible.",
        "Wrap lines": "Fit long messages to the pane width. Each message can take up more vertical space.",
        Timestamps: "Show the time or simulation frame printed by the engine when available.",
        "Collapse repeats": "Show consecutive identical messages once with a repeat count. The saved text still includes every line.",
        "Fold startup blocks": "Hide the contents of recognized startup blocks to make the log easier to scan. Expand a block when you need its details.",
        "Copy shown": "Copy visible messages with their original line numbers. If you selected line numbers, copy only the visible messages in that range.",
        "Save text": "Download the log text, including lines hidden by filters. Large logs are downloaded one section at a time.",
        Reload: "Read the current log section from disk again so you can see newly written messages.",
        "Jump to crash": "Open the part of the log where the engine recorded the crash.",
        "Previous section": "Load the preceding block of the log. Search and filters apply to the section you are viewing.",
        "Next section": "Load the following block of the log. Search and filters apply to the section you are viewing.",
        "Clear filters": "Show all message types and clear the search so hidden lines can appear again."
    };
    function logTip(node, text) { return window.StudioTips?.set(node, text) || node; }

    function actionButton(label, onClick, cls = "button") {
        const node = el("button", cls, label);
        node.type = "button";
        node.addEventListener("click", onClick);
        return logTip(node, logTips[label]);
    }
    function toggle(label, checked, onChange) {
        const wrap = el("label", "logv-toggle"), input = el("input");
        input.type = "checkbox";
        input.checked = checked;
        input.addEventListener("change", () => onChange(input.checked));
        wrap.append(logTip(input, logTips[label]), document.createTextNode(" " + label));
        return wrap;
    }
    function loadPrefs() {
        const defaults = {wrap: true, times: true, collapse: false, fold: true, context: 0};
        try { return {...defaults, ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}")}; }
        catch (_) { return defaults; }
    }
    function savePrefs(prefs) {
        try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* Preferences are optional. */ }
    }
    const GLYPHS = {error: "E", warn: "W"};
    const FILTERS = [["all", "All"], ["error", "Errors"], ["warn", "Warnings"], ["ai", "AI / AngelScript"], ["other", "Other"]];
    const SEVERITY_WORDS = {error: "Error: ", warn: "Warning: "};

    function createViewer({runId, sources, fetchPage}) {
        const prefs = loadPrefs(), first = {offset: 0, firstLine: 1};
        const lifetime = new AbortController();
        let file = sources[0][0], request, loading = false, failure = "", missing = false;
        let section = first, nextSection = null, history = [], endsWithNewline = false, crashLine = null;
        let entries = [], rawLines = [], counts = countEntries([]), filter = "all", pattern = null, view = {rows: [], matched: 0};
        let rowEls = [], errorEls = [], hitEls = [], hitRows = [], hitIndex = -1, currentHit = null;
        let anchor = null, selStart = null, selEnd = null, searchTimer = 0, notice = "";
        const expanded = new Set();

        const root = el("div", "logv");
        const fileGroup = el("div", "logv-seg");
        fileGroup.setAttribute("role", "group");
        fileGroup.setAttribute("aria-label", "Log file");
        const fileButtons = sources.map(([name, label]) => {
            const node = actionButton(label, () => switchFile(name));
            logTip(node, `Read ${name} for this run. Search and filters use the selected file.`);
            fileGroup.append(node);
            return [name, node];
        });
        const searchLabel = el("label", "sr", "Search log");
        const search = el("input");
        search.type = "search"; search.placeholder = "Find in log"; search.autocomplete = "off"; search.spellcheck = false;
        search.id = "logv-search-" + Math.random().toString(36).slice(2, 8);
        searchLabel.htmlFor = search.id;
        logTip(search, "Find text in the log. Use Enter or Shift+Enter to move through hits. For a large log, navigation can load a neighboring section.");
        const hitCounter = el("span", "logv-count", "");
        hitCounter.setAttribute("role", "status");
        const previousHit = actionButton("↑", () => goHit(-1));
        const nextHit = actionButton("↓", () => goHit(1));
        previousHit.setAttribute("aria-label", "Previous match"); nextHit.setAttribute("aria-label", "Next match");
        const pillButtons = FILTERS.map(([kind, label]) => [kind, label, actionButton(label, () => {
            filter = kind; notice = ""; selectionReset(); render(); startHit();
        })]);
        const contextSelect = el("select");
        for (const amount of [0, 2, 5, 10]) {
            const option = el("option", "", amount ? `±${amount} lines` : "No context");
            option.value = String(amount);
            contextSelect.append(option);
        }
        contextSelect.value = String(prefs.context);
        contextSelect.setAttribute("aria-label", "Context lines around filtered results");
        logTip(contextSelect, "Show this many neighboring lines on each side of a filtered result, even if those lines are a different message type.");
        contextSelect.addEventListener("change", () => { prefs.context = Number(contextSelect.value); savePrefs(prefs); render(); });
        const jumpSelect = el("select");
        jumpSelect.setAttribute("aria-label", "Jump to");
        logTip(jumpSelect, "Go directly to a location such as game start or the first error. The viewer may load a different section.");
        for (const [value, label] of [["", "Jump to…"], ["top", "Top"], ["start", "Game start"], ["error", "First error"], ["end", "End"]]) {
            const option = el("option", "", label);
            option.value = value;
            jumpSelect.append(option);
        }
        jumpSelect.addEventListener("change", () => { jump(jumpSelect.value); jumpSelect.value = ""; });
        const previousError = actionButton("▲ Error", () => goProblem(-1));
        const nextError = actionButton("▼ Error", () => goProblem(1));
        const wrapToggle = toggle("Wrap lines", prefs.wrap, value => { prefs.wrap = value; savePrefs(prefs); applyClasses(); });
        const timeToggle = toggle("Timestamps", prefs.times, value => { prefs.times = value; savePrefs(prefs); applyClasses(); render(); });
        const collapseToggle = toggle("Collapse repeats", prefs.collapse, value => { prefs.collapse = value; savePrefs(prefs); render(); });
        const foldToggle = toggle("Fold startup blocks", prefs.fold, value => { prefs.fold = value; savePrefs(prefs); render(); });
        const copy = actionButton("Copy shown", copyLines), download = actionButton("Save text", downloadFile);
        const range = el("fieldset", "logv-range");
        range.hidden = true;
        function lineInput(label) {
            const field = el("label", "", label), input = el("input");
            Object.assign(input, {type: "number", min: 1, step: 1, required: true});
            field.append(input);
            range.append(field);
            return input;
        }
        range.append(el("legend", "", "Log line range"));
        const fromLine = lineInput("First line"), toLine = lineInput("Last line");
        range.append(actionButton("Select range", () => {
            if (!fromLine.reportValidity() || !toLine.reportValidity()) return;
            if (fromLine.valueAsNumber > toLine.valueAsNumber) {
                announce("First line must be at or before last line.");
                fromLine.focus();
                return;
            }
            anchor = selStart = fromLine.valueAsNumber;
            selEnd = toLine.valueAsNumber;
            notice = "";
            paintSelection();
            updateStatus();
        }));
        const rangeToggle = actionButton("Select lines", () => {
            range.hidden = !range.hidden;
            rangeToggle.setAttribute("aria-expanded", String(!range.hidden));
            if (!range.hidden) fromLine.focus();
        });
        rangeToggle.setAttribute("aria-expanded", "false");
        range.id = search.id + "-range";
        rangeToggle.setAttribute("aria-controls", range.id);
        const reload = actionButton("Reload", () => loadFile(section));
        const crash = actionButton("Jump to crash", jumpToCrash, "button logv-crash");
        crash.hidden = true;
        const options = el("details", "logv-options");
        const optionList = el("div", "logv-option-list");
        optionList.append(wrapToggle, timeToggle, collapseToggle, foldToggle, previousError, nextError, download, reload);
        options.append(logTip(el("summary", "button", "View"),
            "Open display options, error navigation, and commands for saving or reloading log text."), optionList);

        const bar1 = el("div", "logv-bar"), bar2 = el("div", "logv-bar");
        const findGroup = el("div", "logv-find");
        findGroup.append(searchLabel, search, previousHit, nextHit, hitCounter);
        bar1.append(fileGroup, findGroup);
        const pills = el("div", "logv-pills");
        pills.append(...pillButtons.map(([, , node]) => node));
        const spacer = el("span", "logv-spacer");
        bar2.append(pills, crash, spacer, contextSelect, jumpSelect, options, rangeToggle, copy);
        const banner = el("div", "logv-banner");
        banner.hidden = true;
        const body = el("div", "logv-body"), scroller = el("div", "logv-scroll"), rowsBox = el("div", "logv-rows");
        const map = el("div", "logv-map");
        scroller.tabIndex = 0;
        scroller.setAttribute("role", "region");
        scroller.setAttribute("aria-label", "Log lines");
        logTip(scroller, "Use Select lines to set a range by line number, or click and shift-click the line numbers.");
        logTip(map, "Click the overview strip to scroll to that part of the visible log section.");
        map.setAttribute("aria-hidden", "true");
        scroller.append(rowsBox);
        body.append(scroller, map);
        const status = el("span", "logv-status");
        status.setAttribute("role", "status");
        const foot = el("div", "logv-foot");
        const sectionNav = el("div", "logv-section-nav");
        const backSection = actionButton("Previous section", () => moveSection(-1));
        const forwardSection = actionButton("Next section", () => moveSection(1));
        sectionNav.append(backSection, forwardSection);
        foot.append(status, sectionNav);
        root.append(bar1, bar2, range, banner, body, foot);

        function applyClasses() {
            root.classList.toggle("wrap", prefs.wrap);
        }

        function startHit() {
            hitIndex = hitEls.length ? 0 : -1;
            if (hitIndex >= 0) markCurrent(); else updateCounter();
        }
        function updateCounter() {
            hitCounter.textContent = !pattern ? "" : hitEls.length ? `${hitIndex + 1} / ${hitEls.length}` : "No matches";
        }
        function scrollTo(node, offsetRatio = 1 / 3) {
            const nodeBox = node.getBoundingClientRect(), box = scroller.getBoundingClientRect();
            scroller.scrollTop += nodeBox.top - box.top - scroller.clientHeight * offsetRatio;
            if (node.matches("mark") && (nodeBox.left < box.left || nodeBox.right > box.right)) {
                scroller.scrollLeft += nodeBox.left - box.left - 80;
            }
        }
        function markCurrent() {
            if (currentHit) currentHit.classList.remove("cur");
            currentHit = hitEls[hitIndex] || null;
            if (currentHit) { currentHit.classList.add("cur"); scrollTo(currentHit); }
            updateCounter();
        }
        function goHit(delta) {
            if (!pattern || loading) return;
            const index = hitIndex + delta;
            if ((index < 0 && history.length) || (index >= hitEls.length && nextSection)) {
                moveSection(delta, true);
                return;
            }
            if (!hitEls.length) return;
            hitIndex = (index + hitEls.length) % hitEls.length;
            markCurrent();
        }
        async function moveSection(direction, seek = false) {
            const query = pattern;
            while (!loading && (direction > 0 ? nextSection : history.length)) {
                let cursor;
                if (direction > 0) {
                    cursor = nextSection;
                    history.push(section);
                } else cursor = history.pop();
                if (!await loadFile(cursor)) return;
                if (!seek || pattern !== query) return;
                if (hitEls.length) {
                    if (direction < 0) {
                        hitIndex = hitEls.length - 1;
                        markCurrent();
                    }
                    return;
                }
            }
            if (seek) announce(direction > 0 ? "No later matches" : "No earlier matches");
        }
        function flash(node) {
            node.classList.remove("flash");
            void node.offsetWidth;
            node.classList.add("flash");
        }
        function goProblem(direction) {
            if (!errorEls.length) { announce("No errors in this view."); return; }
            const here = scroller.scrollTop + scroller.clientHeight / 3;
            const target = direction > 0
                ? errorEls.find(node => node.offsetTop > here + 4) || errorEls[0]
                : [...errorEls].reverse().find(node => node.offsetTop < here - 4) || errorEls[errorEls.length - 1];
            scrollTo(target);
            flash(target);
        }
        function jump(kind) {
            if (!rowEls.length) return;
            if (kind === "top") scroller.scrollTop = 0;
            else if (kind === "end") scroller.scrollTop = scroller.scrollHeight;
            else if (kind === "error") { if (errorEls.length) { scrollTo(errorEls[0]); flash(errorEls[0]); } else announce("No errors in this view."); }
            else if (kind === "start") {
                const index = view.rows.findIndex(item => !item.gap && item.entry.frame !== null && item.entry.frame >= 0);
                if (index >= 0) { scrollTo(rowEls[index]); flash(rowEls[index]); }
                else announce("No in-game lines (frame 0 or later) in this view.");
            }
        }
        function jumpToCrash() {
            if (crashLine === null) return;
            clearTimeout(searchTimer);
            if (filter !== "all" || pattern || search.value) {
                filter = "all";
                search.value = "";
                pattern = null;
                selectionReset();
                render();
            }
            const target = rowEls.find(row => row._n === crashLine);
            if (target) {
                scrollTo(target);
                flash(target);
                announce("Crash at line " + crashLine.toLocaleString());
            }
        }
        function announce(text) { notice = text; updateStatus(); }

        function selectionReset() { anchor = selStart = selEnd = null; }
        function updateRange() {
            const first = entries[0]?.n ?? 1;
            const last = entries.at(-1)?.n ?? first;
            for (const [input, value] of [[fromLine, selStart ?? first], [toLine, selEnd ?? last]]) {
                input.min = first;
                input.max = last;
                input.value = value;
            }
        }
        function paintSelection() {
            for (const node of rowEls) {
                if (node._n === undefined) continue;
                node.classList.toggle("sel", selStart !== null && node._n >= selStart && node._n <= selEnd);
            }
        }
        scroller.addEventListener("click", event => {
            const gutter = event.target.closest && event.target.closest(".ln");
            if (!gutter) return;
            const n = gutter.parentNode._n;
            if (event.shiftKey && anchor !== null) { selStart = Math.min(anchor, n); selEnd = Math.max(anchor, n); }
            else if (selStart === n && selEnd === n) selectionReset();
            else { anchor = n; selStart = selEnd = n; }
            notice = "";
            updateRange();
            paintSelection();
            updateStatus();
        });
        map.addEventListener("click", event => {
            const box = map.getBoundingClientRect();
            const ratio = Math.min(1, Math.max(0, (event.clientY - box.top) / box.height));
            const target = rowEls[Math.min(rowEls.length - 1, Math.floor(ratio * rowEls.length))];
            if (target) scrollTo(target, 0);
        });

        function shownRaw() {
            return view.rows.filter(item => !item.gap && (selStart === null || (item.entry.n >= selStart && item.entry.n <= selEnd)))
                .map(item => item.entry.n + "  " + item.entry.raw);
        }
        async function copyLines() {
            const lines = shownRaw();
            const copiedSection = entries;
            try {
                await navigator.clipboard.writeText(lines.join("\n"));
                if (entries === copiedSection) announce(`Copied ${lines.length.toLocaleString()} line${lines.length === 1 ? "" : "s"}.`);
            } catch (error) {
                if (entries === copiedSection) announce(error instanceof Error ? error.message : String(error));
            }
        }
        function downloadFile() {
            const text = rawLines.join("\n") + (endsWithNewline ? "\n" : "");
            const url = URL.createObjectURL(new Blob([text], {type: "text/plain;charset=utf-8"}));
            const link = el("a");
            link.href = url;
            link.download = section.offset || nextSection ? `${runId}-line-${section.firstLine}-${file}` : `${runId}-${file}`;
            document.body.append(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        }

        function fill(node, text, rowIndex, highlight) {
            for (const part of splitHits(text, highlight ? pattern : null)) {
                if (!part.hit) { node.append(document.createTextNode(part.text)); continue; }
                const mark = el("mark", "", part.text);
                node.append(mark);
                hitEls.push(mark);
                hitRows.push(rowIndex);
            }
        }
        function buildRow(item, index) {
            if (item.gap) return el("div", "lr gap", `⋯ ${item.hidden.toLocaleString()} line${item.hidden === 1 ? "" : "s"} not shown`);
            const entry = item.entry;
            const row = el("div", `lr sev-${entry.severity} cat-${entry.category}` + (entry.continuation ? " cont" : "") +
                (item.context ? " ctx" : ""));
            row._n = entry.n;
            const number = el("span", "ln", String(entry.n)), glyph = el("span", "lg",
                GLYPHS[entry.severity] || (entry.category === "ai_script" ? "A" : ""));
            number.setAttribute("aria-hidden", "true"); glyph.setAttribute("aria-hidden", "true");
            const message = el("span", "lm");
            if (SEVERITY_WORDS[entry.severity]) message.append(el("span", "sr", SEVERITY_WORDS[entry.severity]));
            const highlight = pattern && !item.context;
            const prefix = entry.raw.slice(0, entry.raw.length - entry.body.length);
            if (highlight && (prefs.times || !pattern.test(entry.body))) {
                fill(message, entry.raw, index, true);
            } else {
                if (prefs.times && prefix) {
                    const timestamp = el("span", "lt", prefix);
                    timestamp.title = entry.frame === null ? entry.time : "Frame " + entry.frame + " · " + entry.time;
                    message.append(timestamp);
                }
                fill(message, entry.body, index, highlight);
            }
            if (item.repeat) {
                const badge = el("span", "rep", `×${item.repeat.toLocaleString()}`);
                badge.title = `This message appears ${item.repeat} times; only the first is shown.`;
                message.append(badge);
            }
            if (item.fold) {
                const fold = actionButton(item.fold.open ? "▾ fold" : `▸ ${item.fold.count.toLocaleString()} lines folded`, () => {
                    const restoreFocus = document.activeElement === fold;
                    if (expanded.has(item.fold.index)) expanded.delete(item.fold.index); else expanded.add(item.fold.index);
                    render();
                    if (restoreFocus) (rowsBox.querySelector(`[data-fold-line="${entry.n}"]`) || scroller).focus({preventScroll: true});
                }, "rep fold");
                fold.dataset.foldLine = entry.n;
                logTip(fold, item.fold.open ? "Hide the contents of this startup block. The original lines stay in the log." :
                    "Reveal the hidden startup messages in this block.");
                fold.setAttribute("aria-expanded", String(item.fold.open));
                message.append(fold);
            }
            row.append(number, glyph, message);
            if (entry.severity === "error") errorEls.push(row);
            return row;
        }
        function drawMap() {
            const total = rowEls.length, fragment = document.createDocumentFragment();
            let ticks = 0;
            const add = (index, kind) => {
                if (ticks++ > 3000) return;
                const tick = el("i", "tick " + kind);
                tick.style.top = (index / total * 100).toFixed(3) + "%";
                fragment.append(tick);
            };
            view.rows.forEach((item, index) => {
                if (item.gap) return;
                if (item.entry.severity === "error") add(index, "error");
                else if (item.entry.severity === "warn") add(index, "warn");
            });
            for (const index of hitRows) add(index, "hit");
            map.replaceChildren(fragment);
        }

        function render() {
            const top = scroller.scrollTop;
            rowEls = []; errorEls = []; hitEls = []; hitRows = []; currentHit = null;
            view = computeView(entries, {filter, pattern, context: Number(prefs.context), collapse: prefs.collapse,
                fold: prefs.fold, expanded});
            const fragment = document.createDocumentFragment();
            view.rows.forEach((item, index) => {
                const row = buildRow(item, index);
                rowEls.push(row);
                fragment.append(row);
            });
            if (!view.rows.length) {
                const empty = el("div", "logv-empty", loading ? "Loading…" : missing ? "This file was not generated for this run."
                    : failure ? failure : !entries.length ? "This file is empty." : "No lines match the current filters.");
                if (!loading && entries.length && !failure) {
                    empty.append(document.createTextNode(" "), actionButton("Clear filters", () => {
                        filter = "all"; search.value = ""; pattern = null; render();
                    }));
                }
                fragment.append(empty);
            }
            rowsBox.replaceChildren(fragment);
            scroller.scrollTop = top;
            hitIndex = Math.min(hitIndex, hitEls.length - 1);
            if (!pattern) hitIndex = -1;
            currentHit = hitEls[hitIndex] || null;
            currentHit?.classList.add("cur");
            paintSelection();
            drawMap();
            updateCounter();
            updateControls();
            updateStatus();
        }

        function updateControls() {
            const idle = !loading && !failure && entries.length > 0;
            for (const [name, node] of fileButtons) {
                node.classList.toggle("primary", name === file);
                node.setAttribute("aria-pressed", String(name === file));
            }
            for (const [kind, label, node] of pillButtons) {
                node.textContent = `${label} ${counts[kind].toLocaleString()}`;
                node.classList.toggle("primary", kind === filter);
                node.setAttribute("aria-pressed", String(kind === filter));
                node.disabled = !idle;
            }
            previousHit.disabled = !pattern || loading || !(hitEls.length || history.length);
            nextHit.disabled = !pattern || loading || !(hitEls.length || nextSection);
            previousError.disabled = nextError.disabled = !errorEls.length;
            copy.disabled = !view.rows.some(item => !item.gap);
            rangeToggle.disabled = !idle;
            range.disabled = !idle;
            download.disabled = !rawLines.length;
            logTip(download, section.offset || nextSection
                ? "Download all lines in this section as text, including lines hidden by filters. Other sections are separate."
                : "Download the full log as text, including lines hidden by filters.");
            jumpSelect.disabled = contextSelect.disabled = !idle;
            reload.disabled = loading;
            crash.hidden = crashLine === null;
            sectionNav.hidden = !(history.length || nextSection || section.offset);
            backSection.disabled = loading || !history.length;
            forwardSection.disabled = loading || nextSection == null;
            banner.hidden = !(section.continued || nextSection?.continued || view.truncated);
            banner.textContent = section.continued || nextSection?.continued
                ? "A long line continues across a section boundary."
                : view.truncated ? `Showing the first ${MAX_ROWS.toLocaleString()} matching lines.` : "";
        }
        function updateStatus() {
            let text;
            if (loading) text = "Loading…";
            else if (failure) text = failure;
            else if (missing) text = "This file was not generated for this run.";
            else {
                const lines = view.rows.filter(item => !item.gap).length;
                const scoped = Boolean(section.offset || nextSection);
                text = scoped && entries.length
                    ? `Section ${history.length + 1} · Lines ${entries[0].n.toLocaleString()}–${entries[entries.length - 1].n.toLocaleString()} · ${lines.toLocaleString()} shown`
                    : `Showing ${lines.toLocaleString()} of ${entries.length.toLocaleString()} lines`;
                if (pattern) text += ` · ${hitEls.length.toLocaleString()} match${hitEls.length === 1 ? "" : "es"}`;
                if (selStart !== null) text += selStart === selEnd ? ` · line ${selStart} selected`
                    : ` · lines ${selStart}–${selEnd} selected`;
                if (notice) text += ` · ${notice}`;
            }
            status.textContent = text;
            status.classList.toggle("error", Boolean(failure));
            copy.textContent = selStart === null ? "Copy shown" : "Copy selected";
        }

        async function loadFile(cursor, jumpOnOpen = false) {
            request?.abort();
            const controller = new AbortController(), source = file;
            request = controller;
            section = cursor;
            nextSection = crashLine = null;
            loading = true; failure = ""; missing = false; notice = "";
            entries = []; rawLines = []; counts = countEntries([]); hitIndex = -1;
            selectionReset();
            expanded.clear();
            render();
            const load = offset => fetchPage(source, offset, controller.signal);
            try {
                const result = await readSection(load, section, count => {
                    if (!controller.signal.aborted) status.textContent = `Loading… ${count} pages read`;
                });
                if (controller.signal.aborted) return false;
                nextSection = result.next;
                missing = result.missing;
                endsWithNewline = result.endsWithNewline;
                rawLines = result.rows.map(row => row.text);
                entries = buildEntries(result.rows, source === "result.json", section.firstLine);
                counts = countEntries(entries);
                crashLine = entries.find(entry => /^Error:\s+Spring\b.*\bhas crashed\b/i.test(entry.body))?.n ?? null;
            } catch (error) {
                if (controller.signal.aborted) return false;
                failure = error instanceof Error ? error.message : String(error);
            }
            loading = false;
            updateRange();
            render();
            startHit();
            if (jumpOnOpen && !search.value && filter === "all") jumpToCrash();
            return !failure;
        }
        function switchFile(name) {
            if (name === file) return;
            file = name; filter = "all";
            history = [];
            loadFile(first);
        }

        function applySearch() {
            clearTimeout(searchTimer);
            const next = searchPattern(search.value);
            if ((next && next.source) === (pattern && pattern.source)) return;
            pattern = next;
            notice = "";
            selectionReset();
            render();
            startHit();
            return true;
        }
        search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(applySearch, 120); });
        function onKey(event) {
            const tag = event.target.tagName;
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
                event.preventDefault(); search.focus(); search.select(); return;
            }
            if (event.target === search) {
                if (event.key === "Enter") {
                    event.preventDefault();
                    if (!applySearch() || !hitEls.length) goHit(event.shiftKey ? -1 : 1);
                }
                else if (event.key === "Escape" && search.value) {
                    event.preventDefault(); event.stopPropagation();
                    search.value = ""; applySearch();
                }
                return;
            }
            if (event.ctrlKey || event.metaKey || event.altKey || tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
            if (event.key === "/") { event.preventDefault(); search.focus(); search.select(); }
            else if (event.key === "n") goHit(1);
            else if (event.key === "N") goHit(-1);
            else if (event.key === "e") goProblem(1);
            else if (event.key === "E") goProblem(-1);
            else if (event.key === "g") jump("top");
            else if (event.key === "G") jump("end");
        }

        applyClasses();
        return {
            element: root,
            start() {
                root.closest("dialog")?.addEventListener("keydown", onKey, {signal: lifetime.signal});
                loadFile(first, true);
            },
            destroy() { request?.abort(); clearTimeout(searchTimer); lifetime.abort(); },
        };
    }

    return {
        parseLine, severityOf, normalizeKey, joinPages, readSection, buildEntries, countEntries, searchPattern,
        splitHits, computeView, createViewer, MAX_ROWS,
    };
})();
