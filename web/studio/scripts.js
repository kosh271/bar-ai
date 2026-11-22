"use strict";

window.StudioScripts = (() => {
    function mask(text, keepStrings = false) {
        const chars = text.split("");
        let i = 0;
        while (i < text.length) {
            if (text[i] === '"' || text[i] === "'") {
                const start = i, quote = text[i]; chars[i++] = " ";
                let closed = false;
                while (i < text.length) {
                    if (text[i] === "\\") {
                        chars[i++] = " ";
                        if (i < text.length) chars[i++] = " ";
                    } else if (text[i] === quote) { chars[i++] = " "; closed = true; break; }
                    else { if (!"\r\n".includes(text[i])) chars[i] = " "; i++; }
                }
                if (!closed) throw Error("Unterminated script string.");
                if (keepStrings) for (let j = start; j < i; j++) chars[j] = text[j];
            } else if (text.slice(i, i + 2) === "//") {
                while (i < text.length && !"\r\n".includes(text[i])) chars[i++] = " ";
            } else if (text.slice(i, i + 2) === "/*") {
                chars[i++] = " "; chars[i++] = " ";
                while (i < text.length && text.slice(i, i + 2) !== "*/") {
                    if (!"\r\n".includes(text[i])) chars[i] = " ";
                    i++;
                }
                if (i >= text.length) throw Error("Unterminated script comment.");
                chars[i++] = " "; chars[i++] = " ";
            } else i++;
        }
        return chars.join("");
    }

    function includePath(current, name) {
        if (!name) throw Error("Expected a quoted relative filename after #include.");
        if (/[\\:\x00-\x1f]/.test(name) || name.startsWith("/") || !/\.as$/i.test(name)) {
            throw Error("Unsupported script include path: " + name);
        }
        const parts = current.split("/").slice(0, -1);
        for (const part of name.split("/")) {
            if (!part || part === ".") continue;
            if (part === "..") {
                if (parts.length <= 1) throw Error("Script include is outside the profile script folder: " + name);
                parts.pop();
            } else parts.push(part);
        }
        return parts.join("/");
    }

    function includeLines(file, clean) {
        const source = mask(file.text, true);
        return [...source.matchAll(/^[ \t]*#[ \t]*include\b([^\r\n]*)/gm)]
            .filter(match => clean[match.index + match[0].indexOf("#")] === "#")
            .map(match => ({offset: match.index, end: match.index + match[0].length,
                name: match[1].trim().match(/^"([^"]+)"$/)?.[1]}));
    }

    function resolve(files, path, {all = false} = {}) {
        const entries = new Map(files.filter(file => /\.as$/i.test(file.path))
            .map(file => [file.path.replace(/\\/g, "/"), file]));
        if (path && !entries.has(path)) return null;
        const issues = [], visited = new Set(), active = new Set(), reachable = [];
        function issue(file, code, offset, message) {
            issues.push({path: file.path, line: file.text.slice(0, offset).split("\n").length, code, message});
        }
        function read(file) {
            const clean = mask(file.text);
            return {clean, includes: includeLines(file, clean)};
        }
        function target(current, include) {
            const name = includePath(current, include.name);
            if (!entries.has(name)) throw Error("Included script not found: " + name);
            return name;
        }
        function visit(current) {
            if (visited.has(current)) return;
            visited.add(current); active.add(current);
            const file = entries.get(current);
            reachable.push(file);
            let includes = [];
            try { includes = read(file).includes; }
            catch (error) { issue(file, "script-syntax", 0, error.message); }
            for (const include of includes) {
                let next;
                try { next = target(current, include); }
                catch (error) {
                    issue(file, error.message.startsWith("Included script not found") ? "include-missing" : "include-path",
                        include.offset, error.message);
                    continue;
                }
                if (active.has(next)) {
                    const chain = [...active];
                    issue(file, "include-cycle", include.offset,
                        "Script include cycle: " + chain.slice(chain.indexOf(next)).concat(next).join(" → "));
                } else visit(next);
            }
            active.delete(current);
        }
        if (all) {
            if (path) visit(path);
            else for (const current of entries.keys()) visit(current);
            return {files: reachable, issues};
        }
        const chain = [];
        for (let current = path; ; ) {
            if (chain.includes(current)) throw Error("Script include cycle: " + chain.concat(current).join(" → "));
            chain.push(current);
            const file = entries.get(current), {clean, includes} = read(file);
            if (includes.length !== 1) return {file, chain};
            const include = includes[0];
            if (clean.slice(0, include.offset).trim() || clean.slice(include.end).trim()) return {file, chain};
            current = target(current, include);
        }
    }
    const layerOptions = new Map([
        ["experimentallegionfaction", "Legion enabled"],
        ["scavunitsforplayers", "Scavenger units"],
        ["experimentalextraunits", "Extra units"],
        ["scav_swarmmode", "Swarm"]
    ]);
    const flowKeywords = new Set(["if", "else", "for", "while", "switch", "do", "try", "catch"]);

    function layerPresets(files) {
        const entry = "script/init.as";
        const graph = resolve(files, entry, {all: true});
        const warnings = graph?.issues.slice() || [];
        const presets = [];
        const warn = (path, line, message) => warnings.push({path, line, message});
        if (!graph) {
            warn(entry, 1, "This profile has no init.as to inspect.");
            return {presets, warnings};
        }
        const available = new Set(files.map(file => file.path.replace(/\\/g, "/")));
        const functions = new Map(), operations = [], active = new Set();
        const lineOf = (file, offset) => file.text.slice(0, offset).split("\n").length;
        const usedOptions = new Set(), customBranches = [];
        function warnAt(file, offset, message) { warn(file.path, lineOf(file, offset), message); }

        for (const file of graph.files) {
            let source;
            try {
                const sourceChars = mask(file.text, true).split("");
                for (const include of includeLines(file, mask(file.text))) sourceChars.fill(" ", include.offset, include.end);
                source = sourceChars.join("");
            } catch (_) { continue; }
            const tokens = [...source.matchAll(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[A-Za-z_]\w*|==|!=|::|[^\s]/g)]
                .map(match => ({value: match[0], offset: match.index}));
            const opens = new Map(Object.entries({"(": ")", "{": "}", "[": "]"}));
            const closes = new Set(opens.values()), stack = [], ends = new Map();
            let mismatch;
            tokens.some(({value, offset}, index) => {
                if (opens.has(value)) stack.push(index);
                if (closes.has(value)) {
                    const start = stack.pop();
                    if (opens.get(tokens[start]?.value) !== value) { mismatch = offset; return true; }
                    ends.set(start, index);
                }
                return false;
            });
            if (mismatch !== undefined || stack.length) {
                warnAt(file, mismatch ?? tokens[stack[0]].offset, "Unbalanced delimiters prevent static layer analysis in this script.");
                continue;
            }
            for (let i = 1; i < tokens.length - 2; i++) {
                const name = tokens[i].value;
                if (!/^[A-Za-z_]\w*$/.test(name) || flowKeywords.has(name) ||
                    !/^[A-Za-z_]\w*$/.test(tokens[i - 1].value) || tokens[i + 1].value !== "(") continue;
                const start = ends.get(i + 1) + 1;
                if (tokens[start]?.value !== "{") continue;
                const info = {file, tokens, ends, start, end: ends.get(start), source};
                if (!functions.has(name)) functions.set(name, []);
                functions.get(name).push(info);
                i = info.end;
            }
        }

        function modCondition(source, aliases) {
            const condition = source.replaceAll(/\s/g, "");
            const match = condition.match(/^string\((aiSetupMgr\.GetModOptions\(\)|\w+)\["(\w+)"\]\)(==|!=)"([01])"$/);
            if (!match || (match[1] !== "aiSetupMgr.GetModOptions()" && !aliases.has(match[1])) ||
                !layerOptions.has(match[2])) return null;
            return {key: match[2], enabled: (match[3] === "==") === (match[4] === "1")};
        }

        function record(file, offset, values, conditions, replace) {
            const paths = [];
            for (const name of values) {
                if (!/^(?:config\/)?(?:[\w-]+\/)*[\w-]+(?:\.json)?$/.test(name)) {
                    warnAt(file, offset, "Unsupported layer filename: " + name);
                    return;
                }
                const path = (name.startsWith("config/") ? name : "config/" + name) + (name.endsWith(".json") ? "" : ".json");
                if (!available.has(path)) warnAt(file, offset, "Layer is unavailable in this profile: " + path);
                else paths.push(path);
            }
            for (const condition of conditions) if (layerOptions.has(condition.key)) usedOptions.add(condition.key);
            operations.push({paths, conditions, replace});
        }

        let statementsLeft = 10000;
        function scan(name, conditions, caller, depth = 0) {
            const candidates = functions.get(name);
            if (candidates?.length !== 1 || active.has(name) || depth >= 24) {
                if (caller) warnAt(caller.file, caller.offset, "Cannot determine the effect of helper " + name + "().");
                return;
            }
            active.add(name);
            const {file, tokens, ends, start, end, source} = candidates[0];
            const aliases = new Set();
            const value = index => tokens[index]?.value;
            function statementEnd(index, maximum) {
                for (let i = index; i < maximum; i++) {
                    if (value(i) === ";") return i + 1;
                    if (ends.has(i)) i = ends.get(i);
                }
                return maximum;
            }
            function statement(index, maximum, branch, nesting = 0) {
                if (index >= maximum) return maximum;
                if (statementsLeft <= 0) return maximum;
                if (--statementsLeft === 0) {
                    warnAt(file, tokens[index].offset, "Startup helper expansion exceeds the layer analysis limit.");
                    return maximum;
                }
                if (nesting >= 32) {
                    warnAt(file, tokens[index].offset, "Nested control flow exceeds the layer analysis limit.");
                    return maximum;
                }
                const token = tokens[index], indent = nesting + 1;
                if (token.value === "{") {
                    const close = ends.get(index);
                    list(index + 1, close, branch, indent);
                    return close + 1;
                }
                if (token.value === "if" && value(index + 1) === "(") {
                    const close = ends.get(index + 1);
                    const conditionText = source.slice(tokens[index + 1].offset + 1, tokens[close].offset).trim();
                    const known = modCondition(conditionText, aliases);
                    const key = known?.key ?? file.path + ":" + token.offset;
                    const condition = {key, enabled: known?.enabled ?? true};
                    const count = operations.length;
                    let next = statement(close + 1, maximum, [...branch, condition], indent);
                    if (value(next) === "else") {
                        next = statement(next + 1, maximum, [...branch, {...condition, enabled: !condition.enabled}], indent);
                    }
                    if (!known && operations.length > count) {
                        warnAt(file, token.offset, "Custom layer condition requires engine verification: " + conditionText);
                        for (const operation of operations.slice(count)) {
                            const enabled = operation.conditions.find(item => item.key === key)?.enabled;
                            if (enabled !== undefined) customBranches.push({conditions: operation.conditions,
                                label: (enabled ? "" : "else for ") + conditionText});
                        }
                    }
                    return next;
                }
                if (flowKeywords.has(token.value)) {
                    let body = index + 1;
                    if (value(body) === "(") body = ends.get(body) + 1;
                    const next = value(body) === "{" ? ends.get(body) + 1 : statementEnd(body, maximum);
                    if (/\b(?:profile|LoadConfig)\b/.test(mask(source.slice(token.offset, tokens[next]?.offset ?? source.length)))) {
                        warnAt(file, token.offset, "Layer changes inside " + token.value + " require engine verification.");
                    } else {
                        for (const item of tokens.slice(body, next)) {
                            if (functions.has(item.value)) {
                                warnAt(file, token.offset, "Helper calls inside " + token.value + " require engine verification.");
                                break;
                            }
                        }
                    }
                    if (token.value === "do" && value(next) === "while") return statementEnd(next, maximum);
                    return next;
                }
                const next = statementEnd(index, maximum);
                const statementText = source.slice(token.offset, tokens[next - 1]?.offset + value(next - 1).length).trim();
                const alias = statementText.match(/^dictionary\s*@?\s*(\w+)\s*=\s*aiSetupMgr\s*\.\s*GetModOptions\s*\(\s*\)\s*;$/);
                if (alias && !branch.length) aliases.add(alias[1]);
                const reassignment = statementText.match(/^@?\s*(\w+)\s*=(?!=)/);
                if (reassignment) aliases.delete(reassignment[1]);
                if (token.value === "return" && branch.length) {
                    warnAt(file, token.offset, "Conditional return may change the layer order at runtime.");
                }
                const assignment = statementText.match(/^@?\s*(\w+)\s*\.\s*profile\s*=\s*@\s*\(\s*array\s*<\s*string\s*>\s*=\s*\{([^{}]*)\}\s*\)\s*;$/);
                const append = statementText.match(/^(\w+)\s*\.\s*profile\s*\.\s*insertAt\s*\(\s*\1\s*\.\s*profile\s*\.\s*length\s*\(\s*\)\s*,\s*\{([^{}]*)\}\s*\)\s*;$/);
                const load = statementText.match(/^(?:\w+\s*\.\s*)?LoadConfig\s*\(\s*("[^"\\]*")\s*\)\s*;$/);
                const insertLast = statementText.match(/^\w+\s*\.\s*profile\s*\.\s*insertLast\s*\(\s*("[^"\\]*")\s*\)\s*;$/);
                if (assignment || append || load || insertLast) {
                    const literal = assignment?.[2] ?? append?.[2] ?? load?.[1] ?? insertLast[1];
                    if (/^\s*(?:"[^"\\]*"\s*(?:,\s*"[^"\\]*"\s*)*,?\s*)?$/.test(literal)) {
                        record(file, token.offset, [...literal.matchAll(/"([^"]*)"/g)].map(item => item[1]), branch, !!assignment);
                    } else warnAt(file, token.offset, "Computed layer names require engine verification.");
                } else if (/\b(?:profile|LoadConfig)\b/.test(mask(statementText))) {
                    warnAt(file, token.offset, "Dynamic layer operation requires engine verification.");
                } else {
                    const call = token.value === "return" ? index + 1 : index;
                    if (value(call + 1) === "(" && ends.get(call + 1) === next - 2 && functions.has(value(call))) {
                        scan(value(call), branch, {file, offset: token.offset}, depth + 1);
                    }
                }
                return next;
            }
            function list(first, last, branch, nesting) {
                for (let i = first; i < last;) {
                    const next = statement(i, last, branch, nesting);
                    if (value(i) === "return") return;
                    if (next <= i) return;
                    i = next;
                }
            }
            list(start + 1, end, conditions, 0);
            active.delete(name);
        }
        if (functions.get("AiInit")?.length !== 1) warn(entry, 1, "Could not locate a unique AiInit() function.");
        else scan("AiInit", []);
        if (!operations.length) {
            warn(entry, 1, "No statically recognizable configuration loads were found.");
            return {presets, warnings};
        }
        function add(name, settings) {
            let order = [];
            for (const operation of operations) {
                if (!operation.conditions.every(condition => (settings.get(condition.key) ?? false) === condition.enabled &&
                    (layerOptions.has(condition.key) || settings.has(condition.key)))) continue;
                if (operation.replace) order = [];
                order.push(...operation.paths);
            }
            if (order.length) presets.push({name, order});
        }
        add("Standard (Armada / Cortex)", new Map());
        const options = [...usedOptions];
        for (let combination = 1; combination < 2 ** options.length; combination++) {
            const enabled = options.filter((_, index) => combination & (1 << index));
            add(enabled.map(key => layerOptions.get(key)).join(" + "), new Map(enabled.map(key => [key, true])));
        }
        const seen = new Set();
        for (const {conditions, label} of customBranches) {
            const key = JSON.stringify(conditions);
            if (seen.has(key)) continue;
            seen.add(key);
            add("Detected custom branch: " + label, new Map(conditions.map(condition => [condition.key, condition.enabled])));
        }
        return {presets, warnings};
    }
    return {mask, resolve, layerPresets};
})();
