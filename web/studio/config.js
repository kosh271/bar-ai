"use strict";

// Positions refer to the original text so edits do not discard comments.
window.StudioConfig = (() => {
    function mask(text) {
        const chars = text.split("");
        let i = 0;
        if (chars[0] === "\ufeff") chars[0] = " ";
        while (i < text.length) {
            if (text[i] === '"') {
                i++;
                while (i < text.length) {
                    if (text[i] === "\\") i += 2;
                    else if (text[i++] === '"') break;
                }
            } else if (text[i] === "/" && text[i + 1] === "/") {
                while (i < text.length && !"\r\n".includes(text[i])) chars[i++] = " ";
            } else if (text[i] === "/" && text[i + 1] === "*") {
                chars[i++] = " "; chars[i++] = " ";
                while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) {
                    if (!"\r\n".includes(text[i])) chars[i] = " ";
                    i++;
                }
                if (i >= text.length) throw Error("Unterminated block comment");
                chars[i++] = " "; chars[i++] = " ";
            } else i++;
        }
        return chars.join("");
    }

    function parse(text) {
        const clean = mask(text);
        const validation = clean.split("");
        for (let i = 0; i < clean.length; i++) {
            if (clean[i] === '"') {
                for (i++; i < clean.length; i++) {
                    if (clean[i] === "\\") i++;
                    else if (clean[i] === '"') break;
                }
            } else if (clean[i] === ",") {
                let next = i + 1;
                while (next < clean.length && /\s/.test(clean[next])) next++;
                if (clean[next] === "]" || clean[next] === "}") validation[i] = " ";
            }
        }
        JSON.parse(validation.join(""));
        let pos = 0;
        function space() { while (pos < clean.length && /\s/.test(clean[pos])) pos++; }
        function string() {
            const start = pos++;
            while (pos < clean.length) {
                if (clean[pos] === "\\") pos += 2;
                else if (clean[pos++] === '"') break;
            }
            return JSON.parse(clean.slice(start, pos));
        }
        function read(path) {
            space();
            const start = pos, ch = clean[pos];
            if (ch === "{" || ch === "[") {
                const object = ch === "{", children = [], keys = new Set();
                pos++; space();
                while (clean[pos] !== (object ? "}" : "]")) {
                    let key;
                    if (object) {
                        key = string();
                        if (keys.has(key)) throw Error("Duplicate key: " + path.concat(key).join("."));
                        keys.add(key); space(); pos++;
                    } else key = children.length;
                    children.push({key, node: read(path.concat(key))});
                    space();
                    if (clean[pos] !== ",") break;
                    pos++; space();
                }
                pos++;
                return {kind: object ? "object" : "array", path, start, end: pos, children};
            }
            let value;
            if (ch === '"') value = string();
            else {
                while (pos < clean.length && !/[\s,\]}]/.test(clean[pos])) pos++;
                value = JSON.parse(clean.slice(start, pos));
            }
            return {kind: value === null ? "null" : typeof value, value, path, start, end: pos};
        }
        return read([]);
    }
    function at(node, path) {
        for (const key of path) {
            const child = node && node.children && node.children.find(item => item.key === key);
            if (!child) return null;
            node = child.node;
        }
        return node;
    }
    function patch(text, changes) {
        const sorted = [...changes].sort((a, b) => b.start - a.start);
        let previous = text.length + 1;
        for (const change of sorted) {
            if (change.end > previous || change.start > change.end) throw Error("Overlapping edits");
            text = text.slice(0, change.start) + change.text + text.slice(change.end);
            previous = change.start;
        }
        parse(text);
        return text;
    }
    function scalar(text, node, value) {
        if (!node || node.children || node.kind === "null") throw Error("This value cannot be edited here");
        if (typeof value !== node.kind || (node.kind === "number" && !Number.isFinite(value))) throw Error("Invalid value");
        return patch(text, [{start: node.start, end: node.end, text: JSON.stringify(value)}]);
    }
    function editArrays(text, operations) {
        const root = parse(text), clean = mask(text), changes = [];
        for (const op of operations) {
            const list = at(root, op.path);
            if (!list || list.kind !== "array" || list.children.some(e => e.node.children)) {
                throw Error("This list has an unsupported structure");
            }
            if (clean.slice(list.start, list.end) !== text.slice(list.start, list.end)) {
                throw Error("This list contains comments; edit it in All files to preserve them");
            }
            const values = list.children.map(e => e.node.value);
            if (op.remove !== undefined) {
                if (!Number.isInteger(op.remove) || op.remove < 0 || op.remove >= values.length) throw Error("Invalid list entry");
                values.splice(op.remove, 1);
            } else values.push(op.add);
            changes.push({start: list.start, end: list.end, text: JSON.stringify(values)});
        }
        return patch(text, changes);
    }
    function appendArray(text, path, value) {
        const list = at(parse(text), path);
        if (!list || list.kind !== "array") throw Error("This list is unavailable");
        const last = list.children[list.children.length - 1], closing = list.end - 1;
        const encoded = JSON.stringify(value);
        if (last && last.node.end === closing) return patch(text, [{start: closing, end: closing, text: ", " + encoded}]);
        const changes = [{start: closing, end: closing, text: "\n" + encoded + "\n"}];
        if (last && !mask(text).slice(last.node.end, closing).includes(",")) changes.push({start: last.node.end, end: last.node.end, text: ","});
        return patch(text, changes);
    }
    function changeFactoryUnit(text, factory, unit, index) {
        const root = parse(text), fac = at(root, ["factory", factory]);
        const units = at(fac, ["unit"]);
        if (!units || units.kind !== "array" || units.children.some(e => e.node.kind !== "string")) {
            throw Error("Factory unit list is missing or unsupported");
        }
        if (index === null && (!unit || units.children.some(e => e.node.value === unit))) {
            throw Error("Enter a new, non-duplicate unit ID");
        }
        if (index !== null && (!Number.isInteger(index) || index < 0 || index >= units.children.length)) {
            throw Error("Invalid unit selection");
        }
        const lists = [units];
        for (const situation of ["land", "water", "air"]) {
            const group = at(fac, [situation]);
            if (!group) continue;
            if (group.kind !== "object") throw Error("Unsupported factory situation");
            for (const entry of group.children) {
                if (!/^tier\d+$/.test(entry.key) || entry.node.kind !== "array" ||
                    entry.node.children.length !== units.children.length ||
                    entry.node.children.some(item => item.node.kind !== "number")) {
                    throw Error("Tier lists are unsupported or do not align with the unit list");
                }
                lists.push(entry.node);
            }
        }
        const clean = mask(text), changes = [];
        for (const list of lists) {
            if (index === null) {
                const last = list.children[list.children.length - 1];
                const closing = list.end - 1, value = JSON.stringify(list === units ? unit : 0);
                if (last && last.node.end === closing) {
                    changes.push({start: closing, end: closing, text: ", " + value});
                } else {
                    if (last && !clean.slice(last.node.end, closing).includes(",")) changes.push({start: last.node.end, end: last.node.end, text: ","});
                    changes.push({start: closing, end: closing, text: "\n" + value + "\n"});
                }
            } else {
                if (clean.slice(list.start, list.end) !== text.slice(list.start, list.end)) {
                    throw Error("Cannot remove entries from a list containing comments; use All files");
                }
                const values = list.children.filter((_, i) => i !== index)
                    .map(({node}) => text.slice(node.start, node.end));
                changes.push({start: list.start, end: list.end, text: `[${values.join(",")}]`});
            }
        }
        return patch(text, changes);
    }
    function previewLayers(files) {
        if (!files.length) throw Error("Add at least one configuration file.");
        const ignored = [];
        function fromNode(node, source) {
            const result = {kind: node.kind, source, previous: []};
            if (node.kind === "object") {
                result.children = new Map(node.children.map(({key, node: child}) => [key, fromNode(child, source)]));
            } else if (node.kind === "array") {
                result.value = node.children.map(({node: child}) => plain(fromNode(child, source)));
            } else result.value = node.value;
            return result;
        }
        function plain(node) {
            if (node.kind === "object") {
                return Object.fromEntries([...node.children].map(([key, child]) => [key, plain(child)]));
            }
            return node.value;
        }
        function merge(target, incoming, path) {
            if (target.kind !== "object" || incoming.kind !== "object") {
                ignored.push({path, source: incoming.source, retained: target.source});
                return;
            }
            for (const [key, next] of incoming.children) {
                const current = target.children.get(key);
                if (current?.kind === "object") merge(current, next, path.concat(key));
                else {
                    if (current) next.previous = [...current.previous, {source: current.source, value: current.value}];
                    target.children.set(key, next);
                }
            }
        }
        let root;
        for (const file of files) {
            let parsed;
            try { parsed = fromNode(parse(file.text), file.path); }
            catch (error) { throw Error(`${file.path}: ${error.message}`); }
            if (!root) root = parsed;
            else merge(root, parsed, []);
        }
        return {root, ignored};
    }
    return {parse, at, patch, scalar, editArrays, appendArray, changeFactoryUnit, previewLayers};
})();
