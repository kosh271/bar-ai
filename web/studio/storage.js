"use strict";

function formatStorageBytes(bytes) {
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let value = bytes, unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit++;
    }
    return `${value.toLocaleString(undefined, {maximumFractionDigits: 1})} ${units[unit]}`;
}

function artifactStoragePanel() {
    const details = element("details", "panel");
    const summary = element("summary", "", "Artifact storage");
    summary.setAttribute("role", "button");
    const usage = element("p", "muted"), feedback = element("p");
    usage.setAttribute("role", "status");
    feedback.setAttribute("role", "status");
    const table = experimentTable(["Select", "Run", "Size", "Protection", "Pin"]);
    const tableWrap = experimentTableWrap(table);
    const rows = table.tBodies[0], actions = element("div", "row");
    const selection = new Set();
    let offset = 0, nextOffset = null, busy = false, data;
    const updateControls = () => {
        remove.disabled = busy || !selection.size;
        previous.disabled = busy || offset === 0;
        next.disabled = busy || nextOffset == null;
        refresh.disabled = busy;
    };
    async function load(at = offset) {
        const result = await api("/api/storage?" + new URLSearchParams({offset: at, limit: 25}));
        if (!result.runs.length && at > 0) return load(Math.max(0, at - 25));
        data = result;
        offset = at;
        nextOffset = result.next_offset;
        selection.clear();
        const plural = result.total_runs === 1 ? "run" : "runs";
        usage.textContent = `${result.total_runs} ${plural} · ${formatStorageBytes(result.total_bytes)} · ${result.protected_runs} protected`;
        tableWrap.hidden = !result.runs.length;
        rows.replaceChildren(...result.runs.map(run => {
            const row = element("tr");
            const selectCell = element("td"), input = element("input");
            input.type = "checkbox";
            input.setAttribute("aria-label", `Select ${run.id} for cleanup`);
            input.disabled = busy || run.protected;
            input.addEventListener("change", () => {
                if (input.checked) selection.add(run.id); else selection.delete(run.id);
                updateControls();
            });
            selectCell.append(input);
            const pinCell = element("td");
            const pin = button(run.pinned ? "Unpin" : "Pin", () => operate(async () => {
                await api("/api/storage", {action: "pin", id: run.id, pinned: !run.pinned});
                feedback.textContent = `${run.id} ${run.pinned ? "unpinned" : "pinned"}.`;
                await load();
            }, () => rows.querySelector(`button[data-run="${CSS.escape(run.id)}"]`) || refresh));
            pin.dataset.run = run.id;
            pin.setAttribute("aria-label", `${pin.textContent} ${run.id}`);
            pinCell.append(pin);
            const protections = [run.active && "Operation in progress", run.pinned && "Pinned",
                run.has_replay && "Replay", run.unreadable && "Unreadable"].filter(Boolean);
            row.append(selectCell, element("th", "", run.id), element("td", "", formatStorageBytes(run.bytes)),
                element("td", "", protections.join(", ") || (run.protected ? "Protected" : "—")), pinCell);
            row.cells[1].scope = "row";
            return row;
        }));
    }
    async function operate(action, restoreFocus = null) {
        if (busy) return;
        busy = true;
        updateControls();
        feedback.textContent = "";
        try { await action(); }
        catch (error) { feedback.textContent = errorText(error); }
        finally {
            busy = false;
            updateControls();
            for (const [index, input] of Array.from(rows.querySelectorAll('input[type="checkbox"]')).entries()) {
                input.disabled = data.runs[index].protected;
            }
            restoreFocus?.()?.focus();
        }
    }
    const remove = button("Delete selected artifacts", () => {
        const ids = [...selection];
        if (!ids.length || !confirm(`Delete artifacts for ${ids.length} selected ${ids.length === 1 ? "run" : "runs"}?\n\n${ids.join("\n")}`)) return;
        operate(async () => {
            const result = await api("/api/storage", {action: "cleanup", ids});
            const failures = (result.failed || []).map(item => `${item.id}: ${item.error}`);
            feedback.textContent = [`Deleted artifacts for ${result.removed.length} runs.`, ...failures].join(" ");
            await load();
        }, () => refresh);
    });
    const refresh = button("Refresh storage", () => operate(load, () => refresh));
    const previous = button("Previous artifact page", () => operate(() => load(Math.max(0, offset - 25)),
        () => previous.disabled ? next : previous));
    const next = button("Next artifact page", () => operate(() => load(nextOffset),
        () => next.disabled ? previous : next));
    actions.append(remove, refresh, previous, next);
    details.append(summary, element("p", "muted", "Pin runs you want to keep. Replays are protected automatically. Cleanup is available when the current operation finishes."),
        usage, tableWrap, actions, feedback);
    tableWrap.hidden = true;
    details.addEventListener("toggle", () => { if (details.open && !data) operate(load); });
    updateControls();
    return details;
}
