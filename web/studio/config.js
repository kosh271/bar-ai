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
                        const offset = pos;
                        key = string();
                        if (keys.has(key)) {
                            const error = Error("Duplicate key: " + path.concat(key).join("."));
                            error.offset = offset;
                            throw error;
                        }
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
    function appendEntry(text, container, encoded) {
        const last = container.children[container.children.length - 1], closing = container.end - 1;
        if (last && last.node.end === closing) return patch(text, [{start: closing, end: closing, text: ", " + encoded}]);
        const newline = text.includes("\r\n") ? "\r\n" : "\n";
        const closingIndent = text.slice(text.lastIndexOf("\n", closing - 1) + 1, closing);
        const indent = /^[\t ]*$/.test(closingIndent) ? closingIndent : "";
        const changes = [{start: closing, end: closing, text: "\t" + encoded + newline + indent}];
        if (!indent && closingIndent) changes[0].text = newline + changes[0].text;
        if (last && !mask(text).slice(last.node.end, closing).includes(",")) changes.push({start: last.node.end, end: last.node.end, text: ","});
        return patch(text, changes);
    }
    function appendArray(text, path, value) {
        const list = at(parse(text), path);
        if (!list || list.kind !== "array") throw Error("This list is unavailable");
        return appendEntry(text, list, JSON.stringify(value));
    }
    function editDefenseSequence(text, terrain, index, action) {
        if (!["land", "water"].includes(terrain)) throw Error("Choose a defense terrain");
        const list = at(parse(text), ["porcupine", terrain]);
        if (list?.kind !== "array" || list.children.some(({node}) => node.kind !== "number")) {
            throw Error("This defense sequence is unavailable in the structured editor");
        }
        const row = at(list, [index]);
        if (index !== null && !row) throw Error("This defense sequence row is unavailable");
        const step = action === "up" ? -1 : action === "down" ? 1 : 0;
        if (step || (action && typeof action === "object")) {
            const destination = step ? index + step : action.moveTo;
            if (!row || !Number.isInteger(destination) || destination < 0 || destination >= list.children.length) {
                throw Error("This defense cannot move to that position");
            }
            if (index === destination) return text;
            const first = Math.min(index, destination), last = Math.max(index, destination);
            const rows = list.children.slice(first, last + 1).map(({node}) => node);
            const literals = rows.map(node => text.slice(node.start, node.end));
            const [moved] = literals.splice(index - first, 1);
            literals.splice(destination - first, 0, moved);
            return patch(text, rows.map(({start, end}, offset) => ({start, end, text: literals[offset]})));
        }
        if (action === null) {
            if (!row) throw Error("Choose a defense sequence row to remove");
            const clean = mask(text), after = clean.indexOf(",", row.end);
            const before = clean.lastIndexOf(",", row.start - 1);
            const comma = after !== -1 && after < list.end ? after : before > list.start ? before : -1;
            const changes = [{start: row.start, end: row.end, text: ""}];
            if (comma !== -1) changes.push({start: comma, end: comma + 1, text: ""});
            return patch(text, changes);
        }
        if (!Number.isInteger(action) || action < 0 || action > 0xffffffff) throw Error("Choose a valid defense unit slot");
        return index === null ? appendEntry(text, list, String(action))
            : row.value === action ? text : scalar(text, row, action);
    }
    function editBaseDefense(text, index, entry) {
        const root = parse(text), porcupine = at(root, ["porcupine"]);
        const schedule = at(porcupine, ["base"]);
        if (porcupine?.kind !== "object" || (schedule && schedule.kind !== "array")) {
            throw Error("The base defense schedule has an unsupported structure");
        }
        if (entry !== null) {
            const [slot, seconds] = entry;
            const factions = at(porcupine, ["unit"]);
            if (!Number.isInteger(slot) || slot < 0 || factions?.kind !== "object" ||
                !factions.children.some(({node}) => node.kind === "array" && at(node, [slot])?.kind === "string")) {
                throw Error("Choose a unit slot from this configuration layer");
            }
            if (!Number.isInteger(seconds) || seconds < 0 || seconds > 71582788) {
                throw Error("Enter game time in whole seconds between 0 and 71582788");
            }
            if (index === null) {
                return schedule ? appendEntry(text, schedule, JSON.stringify(entry))
                    : appendEntry(text, porcupine, '"base": ' + JSON.stringify([entry]));
            }
        }
        const row = at(schedule, [index]);
        if (!row) throw Error("This scheduled build is unavailable");
        if (entry !== null) {
            const fields = row.children || [];
            if (fields.length !== 2 || fields.some(({node}) => node.kind !== "number")) {
                throw Error("This scheduled build has an unsupported structure");
            }
            return patch(text, fields.flatMap(({node}, field) => node.value === entry[field] ? [] :
                [{start: node.start, end: node.end, text: String(entry[field])}]));
        }
        const next = at(schedule, [index + 1]), previous = at(schedule, [index - 1]);
        const clean = mask(text);
        const after = clean.indexOf(",", row.end);
        const before = clean.lastIndexOf(",", row.start - 1);
        const comma = after !== -1 && after < (next?.start ?? schedule.end) ? after
            : previous && before >= previous.end ? before : -1;
        const original = text.slice(row.start, row.end);
        const masked = clean.slice(row.start, row.end);
        const retained = original === masked ? "" : original.split("")
            .filter((_, index) => /\s/.test(masked[index])).join("");
        const changes = [{start: row.start, end: row.end, text: retained}];
        if (comma !== -1) changes.push({start: comma, end: comma + 1, text: ""});
        return patch(text, changes);
    }
    const completionBuildCategories = Object.freeze([
        "factory", "nano", "store", "pylon", "energy", "geo",
        "defence", "bunker", "big_gun", "radar", "sonar", "convert", "mex", "mexup",
    ]);
    function addDefenseAction(text, category, triggerId, chainIndex, unitId, direction, distance, chance = 100) {
        if (!completionBuildCategories.includes(category)) throw Error("Choose a supported building category");
        if (typeof triggerId !== "string" || !/^[a-z0-9_]+$/.test(triggerId) ||
            typeof unitId !== "string" || !/^[a-z0-9_]+$/.test(unitId)) {
            throw Error("Enter the completed building and defense building IDs");
        }
        if (!["front", "back", "left", "right"].includes(direction)) throw Error("Choose an offset direction");
        if (!Number.isFinite(distance) || !Number.isFinite(Math.fround(distance))) throw Error("Enter a numeric offset distance");
        if (!Number.isFinite(chance) || chance < 0 || chance > 100) throw Error("Enter a chance from 0 to 100 percent");
        const action = {unit: unitId, category: "defence", offset: {[direction]: distance}};
        if (chance < 100) action.condition = {chance: chance / 100};

        const root = parse(text);
        const keys = ["build_chain", category, triggerId, "hub"];
        let container = root;
        for (const [index, key] of keys.entries()) {
            if (container.kind !== "object") throw Error("This build chain has an unsupported structure");
            const child = at(container, [key]);
            if (!child) {
                if (chainIndex !== null) throw Error("This chain is unavailable");
                const value = keys.slice(index + 1).reduceRight((nested, name) => ({[name]: nested}), [[action]]);
                return appendEntry(text, container, JSON.stringify(key) + ": " + JSON.stringify(value));
            }
            container = child;
        }
        if (container.kind !== "array") throw Error("This build chain has an unsupported structure");
        if (chainIndex === null) return appendEntry(text, container, JSON.stringify([action]));
        const chain = at(container, [chainIndex]);
        if (chain?.kind !== "array") throw Error("This chain is unavailable");
        return appendEntry(text, chain, JSON.stringify(action));
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
    const builtInRoles = new Map(Object.entries({
        BUILDER: "builder", BUILDER2: "builderT2", SCOUT: "scout", RAIDER: "raider", RIOT: "riot",
        ASSAULT: "assault", SKIRM: "skirmish", ARTY: "artillery", AA: "anti_air", AS: "anti_sub",
        AH: "anti_heavy", AHA: "anti_heavy_ass", BOMBER: "bomber", SUPPORT: "support", MINE: "mine",
        TRANS: "transport", AIR: "air", SUB: "sub", STATIC: "static", HEAVY: "heavy", SUPER: "super",
        COMM: "commander", REZZER: "rezzer"
    }));

    function validateProfile(files, catalog = null) {
        const scripts = window.StudioScripts;
        const errors = [], warnings = [], configs = [];
        const unitCatalog = catalog && (catalog.byId || new Map(catalog.units.map(unit => [unit.id.toLowerCase(), unit])));
        const knownRoles = new Set(builtInRoles.values());
        const roleSymbols = new Set(builtInRoles.keys());
        const assignedRoles = new Set(), roleReferences = [];
        function finding(target, code, file, node, message) {
            const offset = node?.start ?? 0;
            target.push({code, path: file.path, location: node?.path?.join(" / ") || "",
                line: file.text.slice(0, offset).split("\n").length, message});
        }
        const children = node => node?.children || [];
        const entries = node => node?.kind === "object" ? node.children : [];
        function unit(file, node, id = node?.value) {
            if (unitCatalog && typeof id === "string" && !/^_.+_$/.test(id) && !unitCatalog.has(id.toLowerCase())) {
                finding(warnings, "unknown-unit", file, node, `Unit "${id}" is absent from the loaded catalog.`);
            }
        }
        function role(file, node, id = node.value) {
            if (typeof id === "string" && !id.startsWith("_")) roleReferences.push({file, node, id});
        }
        function field(file, node, kind, itemKind) {
            if (!node) return false;
            if (node.kind === kind && (!itemKind || node.children.every(item => item.node.kind === itemKind))) return true;
            finding(errors, "config-shape", file, node, `Expected ${itemKind ? "an array of " + itemKind + " values" : "an " + kind}.`);
            return false;
        }

        for (const file of files) {
            if (!/\.json$/i.test(file.path)) continue;
            const path = file.path.replace(/\\/g, "/");
            try {
                const root = parse(file.text);
                if (path.startsWith("config/")) field(file, root, "object");
                configs.push({file, root, path});
            } catch (error) {
                const offset = error.offset ?? Number(error.message.match(/\bposition (\d+)/)?.[1] ?? 0);
                finding(errors, "json-syntax", file, {start: offset}, error.message);
            }
        }

        const graph = scripts.resolve(files, undefined, {all: true});
        warnings.push(...graph.issues);
        for (const file of graph.files) {
            try {
                const clean = scripts.mask(file.text);
                const withStrings = scripts.mask(file.text, true);
                const pattern = /\bTypeMask\s+(\w+)\s*=\s*(?:AiAddRole|aiRoleMasker\s*\.\s*GetTypeMask)\s*\(\s*"([^"\\]+)"/g;
                for (const match of withStrings.matchAll(pattern)) {
                    if (!clean.slice(match.index, match.index + match[0].length).trimStart().startsWith("TypeMask")) continue;
                    roleSymbols.add(match[1]);
                    knownRoles.add(match[2]);
                }
                for (const match of clean.matchAll(/\bType\s+(\w+)\s*=\s*Unit\s*::\s*Role\s*::\s*\w+\s*\.\s*type\b/g)) {
                    roleSymbols.add(match[1]);
                }
            } catch (_) { /* The include scan already reported unreadable source. */ }
        }
        const checkedCommanderFiles = new Set();
        for (const file of graph.files.filter(file => /(?:^|[\\/])commander\.as$/i.test(file.path))) {
            const path = file.path.replace(/\\/g, "/");
            const related = scripts.resolve(files, path, {all: true}).files;
            for (const script of related) {
                if (checkedCommanderFiles.has(script.path)) continue;
                checkedCommanderFiles.add(script.path);
                try {
                    for (const {name, offset} of window.StudioOpenings.roleReferences(script.text)) {
                        if (!roleSymbols.has(name)) {
                            finding(warnings, "unknown-script-role", script, {start: offset, path: []},
                                `RT::${name} has no known role declaration.`);
                        }
                    }
                } catch (error) {
                    finding(warnings, "script-role-scan", script, null, error.message);
                }
            }
        }

        for (const {file, root, path} of configs) {
            const family = path.split("/").pop();
            const factories = at(root, ["factory"]);
            if (/^factory(?:_.*)?\.json$/i.test(family) && field(file, factories, "object")) {
                const basePath = path.replace(/factory(?:_.*)?\.json$/i, "factory.json");
                const base = basePath !== path && configs.find(item => item.path === basePath)?.root;
                for (const fac of entries(factories)) {
                    unit(file, fac.node, fac.key);
                    if (!field(file, fac.node, "object")) continue;
                    const units = at(fac.node, ["unit"]);
                    if (field(file, units, "array", "string")) {
                        const builds = unitCatalog?.get(fac.key.toLowerCase())?.builds;
                        const buildOptions = Array.isArray(builds) && new Set(builds.map(id => id.toLowerCase()));
                        for (const entry of units.children) {
                            unit(file, entry.node);
                            const id = entry.node.value;
                            if (buildOptions && unitCatalog.has(id.toLowerCase()) && !buildOptions.has(id.toLowerCase())) {
                                finding(warnings, "factory-build-option", file, entry.node, `"${id}" is absent from ${fac.key}'s catalog build options.`);
                            }
                        }
                    }
                    const inherited = !units && base && at(base, ["factory", fac.key, "unit"]);
                    const expected = units?.kind === "array" ? units : inherited?.kind === "array" ? inherited : null;
                    for (const situation of ["land", "water", "air"]) {
                        const group = at(fac.node, [situation]);
                        if (!field(file, group, "object")) continue;
                        for (const tier of entries(group).filter(tier => /^tier\d+$/.test(tier.key))) {
                            if (field(file, tier.node, "array", "number") && expected && tier.node.children.length !== expected.children.length) {
                                finding(inherited ? warnings : errors, inherited ? "factory-inherited-alignment" : "factory-alignment",
                                    file, tier.node, `${tier.node.children.length} weights for ${expected.children.length} ${inherited ? "candidate base-layer " : ""}units.`);
                            }
                        }
                    }
                }
            }
            if (/^response(?:_.*)?\.json$/i.test(family)) {
                const response = at(root, ["response"]);
                if (field(file, response, "object")) for (const entry of entries(response)) {
                    if (entry.key.startsWith("_")) continue;
                    if (!field(file, entry.node, "object")) continue;
                    role(file, entry.node, entry.key);
                    const lists = ["vs", "ratio", "importance"].map(key => at(entry.node, [key]));
                    const valid = lists.map((list, index) => field(file, list, "array", index === 0 ? "string" : "number"));
                    if (valid[0]) for (const item of children(lists[0])) role(file, item.node);
                    if (valid.every(Boolean) && lists.some(list => list.children.length !== lists[0].children.length)) {
                        finding(errors, "response-alignment", file, entry.node, `Response list lengths are ${lists.map(list => list.children.length).join(", ")}.`);
                    }
                }
            }
            if (/^behaviour(?:_.*)?\.json$/i.test(family)) {
                const behaviour = at(root, ["behaviour"]);
                if (field(file, behaviour, "object")) for (const entry of entries(behaviour)) {
                    unit(file, entry.node, entry.key);
                    const roles = at(entry.node, ["role"]);
                    if (field(file, roles, "array", "string")) for (const item of roles.children) {
                        role(file, item.node);
                        assignedRoles.add(item.node.value);
                    }
                }
            }
            if (/^block_map(?:_.*)?\.json$/i.test(family)) {
                unit(file, at(root, ["terrain", "analyze"]));
                for (const entry of entries(at(root, ["building", "instance"]))) unit(file, entry.node, entry.key);
            }
            if (/^build_chain(?:_.*)?\.json$/i.test(family)) {
                for (const category of entries(at(root, ["build_chain"]))) {
                    for (const trigger of entries(category.node)) {
                        unit(file, trigger.node, trigger.key);
                        for (const chain of children(at(trigger.node, ["hub"]))) {
                            for (const step of children(chain.node)) unit(file, at(step.node, ["unit"]));
                        }
                    }
                }
                for (const group of [["unit"], ["superweapon", "unit"], ["wall"], ["choke"], ["default"]]) {
                    for (const faction of entries(at(root, ["porcupine", ...group]))) {
                        if (faction.node.kind === "array") for (const entry of faction.node.children) unit(file, entry.node);
                        else unit(file, faction.node);
                    }
                }
            }
            for (const surface of ["land", "water"]) {
                for (const generator of entries(at(root, ["economy", "energy", surface]))) {
                    const bounds = children(generator.node).slice(0, 2).map(entry => entry.node.value);
                    if (generator.node.kind === "array" && bounds.length === 2 && bounds.every(value => typeof value === "number") &&
                        (!bounds.every(Number.isInteger) || bounds[1] < bounds[0])) {
                        finding(errors, "generator-bounds", file, generator.node,
                            `Generator count bounds ${bounds.join(", ")} must be whole numbers with upper ≥ lower.`);
                    }
                }
            }
        }
        for (const {file, node, id} of roleReferences) {
            if (!knownRoles.has(id)) {
                finding(warnings, "unknown-role", file, node, `Role "${id}" has no known declaration.`);
            } else if (!assignedRoles.has(id) && node.path[0] === "response" && node.path[2] === "vs" &&
                configs.some(config => at(config.root, ["behaviour"])?.kind === "object")) {
                finding(warnings, "unassigned-role", file, node, `Role "${id}" has no assignment in this profile's behavior layers.`);
            }
        }
        errors.sort((a, b) => a.path.localeCompare(b.path) || a.line - b.line);
        warnings.sort((a, b) => a.path.localeCompare(b.path) || a.line - b.line);
        return {errors, warnings};
    }
    return {parse, at, patch, scalar, editArrays, appendArray, editBaseDefense, editDefenseSequence,
        completionBuildCategories, addDefenseAction, changeFactoryUnit, previewLayers, validateProfile};
})();
