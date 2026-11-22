"use strict";

window.StudioEditors = (() => {
    function weightShares(weights) {
        const positive = weights.map(weight => Number.isFinite(weight) && weight > 0 ? weight : 0);
        const total = positive.reduce((sum, weight) => sum + weight, 0);
        if (!total) return positive;
        if (Number.isFinite(total)) return positive.map(weight => weight / total * 100);
        const largest = positive.reduce((max, weight) => Math.max(max, weight), 0);
        return weightShares(positive.map(weight => weight / largest));
    }

    const percent = new Intl.NumberFormat(undefined, {style: "percent", maximumFractionDigits: 1});
    function weightPercent(share) {
        return percent.format(share / 100);
    }

    function compareFiles(before, after) {
        const left = new Map(before.map(file => [file.path, file.text]));
        const right = new Map(after.map(file => [file.path, file.text]));
        const files = [];
        for (const path of [...new Set([...left.keys(), ...right.keys()])].sort()) {
            const a = left.get(path) ?? null, b = right.get(path) ?? null;
            if (a === b) continue;
            const file = {path, left: a, right: b, kind: a === null ? "added" : b === null ? "removed" : "modified"};
            if (/\.jsonc?$/i.test(path)) {
                try {
                    const config = window.StudioConfig;
                    const oldRoot = a === null ? null : config.parse(a);
                    const newRoot = b === null ? null : config.parse(b);
                    file.changes = jsonChanges(oldRoot, newRoot, a, b);
                    if (!file.changes.length) file.kind = "formatting";
                } catch (error) {
                    file.parseError = error.message;
                }
            }
            files.push(file);
        }
        return files;
    }

    function jsonChanges(oldRoot, newRoot, before, after) {
        const changes = [], config = window.StudioConfig;
        function factoryUnits(root, path) {
            if (path[0] !== "factory" || typeof path[1] !== "string") return [];
            const ids = [path[1]];
            if (/^tier\d+$/.test(path[3]) && Number.isInteger(path[4])) {
                const unit = config.at(root, ["factory", path[1], "unit", path[4]]);
                if (unit?.kind === "string") ids.push(unit.value);
            }
            return ids;
        }
        function visit(left, right, path) {
            if (left?.children && right?.children && left.kind === right.kind) {
                const a = new Map(left.children.map(entry => [entry.key, entry.node]));
                const b = new Map(right.children.map(entry => [entry.key, entry.node]));
                for (const key of new Set([...a.keys(), ...b.keys()])) visit(a.get(key), b.get(key), [...path, key]);
                return;
            }
            if (left && right && left.kind === right.kind && left.value === right.value) return;
            const unitValues = path.includes("unit") ? [left, right].filter(node => node?.kind === "string").map(node => node.value) : [];
            const units = [...new Set([
                ...factoryUnits(oldRoot, path), ...factoryUnits(newRoot, path), ...unitValues,
                ...(path[0] === "behaviour" && typeof path[1] === "string" ? [path[1]] : []),
                ...(path[0] === "build_chain" && typeof path[2] === "string" ? [path[2]] : [])
            ])];
            changes.push({path, units,
                left: left ? before.slice(left.start, left.end) : null,
                right: right ? after.slice(right.start, right.end) : null});
        }
        visit(oldRoot, newRoot, []);
        return changes;
    }

    function lineDiff(before, after) {
        const lines = text => text === null ? [] : text.split(/\r\n|\r|\n/);
        const left = lines(before), right = lines(after), rows = [];
        let i = 0, j = 0;
        function push(kind) {
            const text = kind === "added" ? right[j] : left[i];
            rows.push({left: kind === "added" ? null : ++i, right: kind === "removed" ? null : ++j, text, kind});
        }
        while (i < left.length && j < right.length && left[i] === right[j]) push("context");
        let endLeft = left.length, endRight = right.length;
        while (i < endLeft && j < endRight && left[endLeft - 1] === right[endRight - 1]) {
            endLeft--;
            endRight--;
        }
        const startLeft = i, startRight = j, height = endLeft - i, width = endRight - j;
        // Bound the LCS matrix so very large rewrites still render as a replacement.
        if (height && width && height * width <= 1_000_000) {
            const stride = width + 1, lengths = new Uint32Array((height + 1) * stride);
            for (let a = height - 1; a >= 0; a--) {
                for (let b = width - 1; b >= 0; b--) {
                    const index = a * stride + b;
                    lengths[index] = left[startLeft + a] === right[startRight + b]
                        ? 1 + lengths[index + stride + 1]
                        : Math.max(lengths[index + stride], lengths[index + 1]);
                }
            }
            while (i < endLeft && j < endRight) {
                const index = (i - startLeft) * stride + j - startRight;
                if (left[i] === right[j]) push("context");
                else push(lengths[index + stride] >= lengths[index + 1] ? "removed" : "added");
            }
        }
        while (i < endLeft) push("removed");
        while (j < endRight) push("added");
        while (i < left.length) push("context");
        const included = new Uint8Array(rows.length);
        rows.forEach((row, index) => {
            if (row.kind !== "context") included.fill(1, Math.max(0, index - 3), index + 4);
        });
        const visible = [];
        rows.forEach((row, index) => {
            if (!included[index]) return;
            if (visible.length && !included[index - 1]) visible.push({kind: "hunk"});
            visible.push(row);
        });
        return visible;
    }

    return {weightShares, weightPercent, compareFiles, lineDiff};
})();
