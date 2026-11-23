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

    function lineCol(text, offset) {
        const bom = text.startsWith("\ufeff") ? 1 : 0;
        const lines = text.slice(bom, offset).split(/\r\n?|\n/);
        return {line: lines.length, column: lines[lines.length - 1].length + 1};
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

    function tokenizeSource(text) {
        const clean = mask(text);
        const sourceChars = mask(text, true).split("");
        for (const include of includeLines({text}, clean)) sourceChars.fill(" ", include.offset, include.end);
        const source = sourceChars.join("");
        const tokens = [...source.matchAll(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|0[xXbBoO][0-9A-Za-z_]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fFuU]?|[A-Za-z_]\w*|==|!=|<=|>=|::|&&|\|\||[^\s]/g)]
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
            const offset = mismatch ?? tokens[stack[0]].offset;
            const error = Error("Unbalanced delimiters in script.");
            error.offset = offset;
            throw error;
        }
        return {clean, source, tokens, ends};
    }

    function modCondition(source, aliases) {
        const condition = source.replaceAll(/\s/g, "");
        const match = condition.match(/^string\((aiSetupMgr\.GetModOptions\(\)|\w+)\["(\w+)"\]\)(==|!=)"([01])"$/);
        if (!match || (match[1] !== "aiSetupMgr.GetModOptions()" && !aliases.has(match[1])) ||
            !layerOptions.has(match[2])) return null;
        return {key: match[2], enabled: (match[3] === "==") === (match[4] === "1")};
    }

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
    const constructionStart = "// BAR AI Studio construction rules ";
    const constructionEnd = "// End BAR AI Studio construction rules";

    function normalizeConstructionRules(rules) {
        if (!Array.isArray(rules) || !rules.length) throw Error("Construction rules must be a nonempty list.");
        const seen = new Set();
        return rules.map(({trigger, target, count, radius}) => {
            if (![trigger, target].every(id => typeof id === "string" && /^[a-z0-9_]+$/.test(id))) {
                throw Error("Enter a BAR unit ID for both buildings.");
            }
            if (trigger === target) throw Error("Choose different trigger and requested buildings.");
            if (!Number.isInteger(count) || count < 1 || count > 10000) {
                throw Error("Use a completion count from 1 to 10000.");
            }
            if (!Number.isInteger(radius) || radius < 0 || radius > 100000) {
                throw Error("Use a whole-number search radius from 0 to 100000.");
            }
            const rule = {trigger, target, count, radius};
            const key = JSON.stringify(rule);
            if (seen.has(key)) throw Error("Duplicate construction rule.");
            seen.add(key);
            return rule;
        });
    }

    function constructionBlock(rules, newline) {
        const entries = rules.map(({trigger, target, count, radius}) =>
            `    BuildRule("${trigger}", "${target}", ${count}, ${radius}.f)`);
        return `\n${constructionStart}${JSON.stringify(rules)}
namespace StudioConstruction {

class Anchor {
    Id id;
    AIFloat3 position;

    Anchor(Id unitId, const AIFloat3& in pos) {
        id = unitId;
        position = pos;
    }
}

class BuildRule {
    string trigger;
    string target;
    uint count;
    float radius;
    uint completed;
    array<Anchor@> anchors;

    BuildRule(const string& in watched, const string& in requested, uint every, float distance) {
        trigger = watched;
        target = requested;
        count = every;
        radius = distance;
        completed = 0;
    }

    void Finish(CCircuitUnit@ unit, const string& in name) {
        if (name != trigger) return;
        AIFloat3 position = unit.GetPos(ai.frame);
        if (radius == 0.f) {
            if (++completed < count) return;
            completed = 0;
        } else {
            array<uint> members;
            for (uint i = 0; i < anchors.length(); i++) {
                const Anchor@ anchor = anchors[i];
                if (anchor.id == unit.id) return;
                float dx = anchor.position.x - position.x;
                float dz = anchor.position.z - position.z;
                if ((dx * dx + dz * dz) <= radius * radius && members.length() + 1 < count) {
                    members.insertLast(i);
                }
            }
            if (members.length() + 1 < count) {
                anchors.insertLast(Anchor(unit.id, position));
                return;
            }
            for (uint n = members.length(); n > 0; n--) anchors.removeAt(members[n - 1]);
        }
        CCircuitDef@ build = ai.GetCircuitDef(target);
        if (build is null || !build.IsAvailable(ai.frame)) return;
        aiBuilderMgr.Enqueue(TaskB::Common(Task::BuildType::DEFENCE, Task::Priority::NORMAL, build, position, 0.f));
        AiLog("Studio construction request: " + target + " near " + trigger);
    }

    void Forget(Id id) {
        for (uint i = 0; i < anchors.length(); i++) {
            if (anchors[i].id == id) {
                anchors.removeAt(i);
                return;
            }
        }
    }
}

array<BuildRule@> rules = {
${entries.join(",\n")}
};
}

namespace Main {
void AiUnitFinished(CCircuitUnit@ unit) {
    const string name = unit.circuitDef.GetName();
    for (uint i = 0; i < StudioConstruction::rules.length(); i++) {
        StudioConstruction::rules[i].Finish(unit, name);
    }
}

void AiUnitDestroyed(CCircuitUnit@ unit) {
    for (uint i = 0; i < StudioConstruction::rules.length(); i++) {
        StudioConstruction::rules[i].Forget(unit.id);
    }
}
}
${constructionEnd}\n`.replace(/\n/g, newline);
    }

    function readConstructionRules(files) {
        const path = "script/main.as";
        const source = resolve(files, path, {all: true});
        if (!source.files.length) throw Error("This profile needs a main.as script to use construction rules.");
        const issue = source.issues.find(item => item.code !== "include-cycle");
        if (issue) throw Error(issue.message);
        const file = source.files[0];
        const newline = file.text.includes("\r\n") ? "\r\n" : "\n";
        const start = file.text.indexOf(constructionStart);
        const end = file.text.indexOf(constructionEnd);
        let original = file.text;
        let rules = [];
        if (start !== -1 || end !== -1) {
            try {
                const metadataEnd = file.text.indexOf(newline, start);
                rules = normalizeConstructionRules(JSON.parse(file.text.slice(start + constructionStart.length, metadataEnd)));
                const block = constructionBlock(rules, newline);
                const blockStart = start - newline.length;
                if (blockStart < 0 || file.text.slice(blockStart, blockStart + block.length) !== block) throw Error();
                original = file.text.slice(0, blockStart) + file.text.slice(blockStart + block.length);
            } catch { throw Error("The generated construction rules script was edited. Restore its generated block to resume visual editing."); }
        }
        for (const candidate of source.files) {
            if (/\b(?:AiUnitFinished|AiUnitDestroyed|StudioConstruction)\b/.test(mask(candidate === file ? original : candidate.text))) {
                throw Error("This profile already defines custom construction callbacks.");
            }
        }
        return {file, original, newline, rules};
    }

    function writeConstructionRules(files, rules) {
        const {original, newline} = readConstructionRules(files);
        if (!rules.length) return original;
        const block = constructionBlock(normalizeConstructionRules(rules), newline);
        // The block starts on a new line even when the original file had no final newline.
        return original + block;
    }

    const barbHooks = new Set([
        "AiInit", "AiMain", "AiUpdate", "AiUnitFinished", "AiUnitDestroyed",
        "AiMakeTask", "AiTaskAdded", "AiTaskRemoved", "AiUnitAdded", "AiUnitRemoved",
        "AiLoad", "AiSave", "AiUpdateEconomy", "AiIsSwitchTime", "AiIsSwitchAllowed",
        "AiGetFactoryToBuild", "AiMakeDefence", "AiIsAirValid", "GetOpenInfo"
    ]);
    const constTypes = new Set(["int", "uint", "float", "double", "bool", "string"]);
    const isIdent = token => !!token && /^[A-Za-z_]\w*$/.test(token.value);

    function parseStudioMeta(rawLines, lineIndex) {
        let i = lineIndex - 1;
        while (i >= 0 && !rawLines[i].trim()) i--;
        if (i < 0) return null;
        const match = rawLines[i].match(/^\s*\/\/\s*@studio\b(.*)$/);
        if (!match) return null;
        const meta = {};
        for (const pair of match[1].matchAll(/([A-Za-z_]\w*)\s*=\s*(?:"([^"]*)"|([^\s]+))/g)) {
            const key = pair[1];
            const val = pair[2] !== undefined ? pair[2] : pair[3];
            if (key === "min" || key === "max") {
                const num = Number(val);
                if (Number.isFinite(num)) meta[key] = num;
            } else if (["label", "page", "description"].includes(key)) {
                meta[key] = val;
            }
        }
        return Object.keys(meta).length ? meta : null;
    }

    function inspectScript(text) {
        const {clean, tokens, ends} = tokenizeSource(text);
        const includes = includeLines({text}, clean)
            .filter(inc => inc.name)
            .map(inc => ({name: inc.name, line: lineCol(text, inc.offset).line, offset: inc.offset}));
        const rawLines = text.split(/\r\n?|\n/);
        const hooks = [];
        const constants = [];

        function parseConstRhs(type, idx) {
            if (type === "bool") {
                const tok = tokens[idx];
                if (!tok || (tok.value !== "true" && tok.value !== "false") || tokens[idx + 1]?.value !== ";") return null;
                return {start: tok.offset, end: tok.offset + tok.value.length, value: tok.value === "true", unit: null, semi: idx + 1};
            }
            if (type === "string") {
                const tok = tokens[idx];
                if (!tok || !/^"[^"\r\n\\]*"$/.test(tok.value) || tokens[idx + 1]?.value !== ";") return null;
                return {start: tok.offset, end: tok.offset + tok.value.length, value: tok.value.slice(1, -1), unit: null, semi: idx + 1};
            }
            let signTok = null, numIdx = idx;
            if (tokens[idx]?.value === "-" || tokens[idx]?.value === "+") {
                signTok = tokens[idx];
                numIdx = idx + 1;
            }
            const numTok = tokens[numIdx];
            if (!numTok || !/^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fFuU]?$/.test(numTok.value)) return null;
            if (signTok && signTok.offset + 1 !== numTok.offset) return null;
            let unit = null, semi = numIdx + 1;
            if (tokens[semi]?.value === "*" &&
                (tokens[semi + 1]?.value === "SECOND" || tokens[semi + 1]?.value === "MINUTE") &&
                tokens[semi + 2]?.value === ";") {
                unit = tokens[semi + 1].value;
                semi = semi + 2;
            } else if (tokens[semi]?.value !== ";") {
                return null;
            }
            const start = signTok ? signTok.offset : numTok.offset;
            const end = numTok.offset + numTok.value.length;
            const value = Number(text.slice(start, end).replace(/[fFuU]$/, ""));
            if (!Number.isFinite(value)) return null;
            return {start, end, value, unit, semi};
        }

        function scanScope(start, end, namespaceName) {
            let atStatementStart = true;
            for (let i = start; i < end;) {
                const value = tokens[i].value;
                if (value === ";") {
                    atStatementStart = true;
                    i++;
                    continue;
                }
                if (atStatementStart && value === "namespace" && isIdent(tokens[i + 1]) && tokens[i + 2]?.value === "{") {
                    const close = ends.get(i + 2);
                    const nested = namespaceName ? `${namespaceName}::${tokens[i + 1].value}` : tokens[i + 1].value;
                    scanScope(i + 3, close, nested);
                    i = close + 1;
                    atStatementStart = true;
                    continue;
                }
                if (value === "{") {
                    i = ends.get(i) + 1;
                    atStatementStart = true;
                    continue;
                }
                if (value === "(") {
                    const closeParen = ends.get(i);
                    if (tokens[closeParen + 1]?.value === "{") {
                        const prev = tokens[i - 1], beforePrev = tokens[i - 2];
                        if (prev && barbHooks.has(prev.value) && beforePrev && ![".", "::"].includes(beforePrev.value)) {
                            hooks.push({
                                namespace: namespaceName,
                                name: prev.value,
                                line: lineCol(text, prev.offset).line,
                                offset: prev.offset
                            });
                        }
                        i = ends.get(closeParen + 1) + 1;
                        atStatementStart = true;
                        continue;
                    }
                    i = closeParen + 1;
                    atStatementStart = false;
                    continue;
                }
                if (value === "[") {
                    i = ends.get(i) + 1;
                    atStatementStart = false;
                    continue;
                }
                if (atStatementStart && value === "const" &&
                    constTypes.has(tokens[i + 1]?.value) &&
                    isIdent(tokens[i + 2]) &&
                    tokens[i + 3]?.value === "=") {
                    const type = tokens[i + 1].value;
                    const name = tokens[i + 2].value;
                    const rhs = parseConstRhs(type, i + 4);
                    if (rhs) {
                        const loc = lineCol(text, rhs.start);
                        const meta = parseStudioMeta(rawLines, loc.line - 1);
                        constants.push({
                            namespace: namespaceName,
                            name,
                            type,
                            value: rhs.value,
                            rawLiteral: text.slice(rhs.start, rhs.end),
                            unit: rhs.unit,
                            start: rhs.start,
                            end: rhs.end,
                            line: loc.line,
                            column: loc.column,
                            meta
                        });
                        i = rhs.semi + 1;
                        atStatementStart = true;
                        continue;
                    }
                }
                atStatementStart = false;
                i++;
            }
        }

        scanScope(0, tokens.length, "");
        return {includes, hooks, constants};
    }

    function patchScriptLiteral(text, entry, newValue) {
        if (!entry || typeof entry.start !== "number" || typeof entry.end !== "number" ||
            text.slice(entry.start, entry.end) !== entry.rawLiteral) {
            throw Error("The script constant no longer matches its source position.");
        }
        let replacement;
        if (entry.type === "bool") {
            if (typeof newValue === "boolean") replacement = newValue ? "true" : "false";
            else if (newValue === "true" || newValue === "false") replacement = newValue;
            else throw Error("Enter true or false.");
        } else if (entry.type === "string") {
            if (typeof newValue !== "string" || /["\\\r\n]/.test(newValue)) {
                throw Error("Enter a single-line string without quotes or backslashes.");
            }
            replacement = `"${newValue}"`;
        } else {
            const raw = typeof newValue === "string" ? newValue.trim() : newValue;
            if (raw === "" || typeof raw === "boolean") throw Error("Enter a valid number.");
            const num = Number(raw);
            if (!Number.isFinite(num)) throw Error("Enter a valid finite number.");
            if (entry.type === "int" && (!Number.isInteger(num) || num < -2147483648 || num > 2147483647)) {
                throw Error("Enter a 32-bit whole integer.");
            }
            if (entry.type === "uint" && (!Number.isInteger(num) || num < 0 || num > 4294967295)) {
                throw Error("Enter a non-negative whole integer.");
            }
            if (entry.meta?.min !== undefined && num < entry.meta.min) {
                throw Error(`Value must be at least ${entry.meta.min}.`);
            }
            if (entry.meta?.max !== undefined && num > entry.meta.max) {
                throw Error(`Value must be at most ${entry.meta.max}.`);
            }
            if (entry.type === "int" || entry.type === "uint") {
                replacement = String(num) + (/[uU]$/.test(entry.rawLiteral) ? entry.rawLiteral.slice(-1) : "");
            } else {
                replacement = String(num);
                if (/[fF]$/.test(entry.rawLiteral)) {
                    if (!/[.eE]/.test(replacement)) replacement += ".0";
                    replacement += entry.rawLiteral.slice(-1);
                } else if (entry.type === "float" && !/[.eE]/.test(replacement)) {
                    replacement += ".0";
                }
            }
        }
        return text.slice(0, entry.start) + replacement + text.slice(entry.end);
    }

    function discoverScriptParameters(files) {
        const params = [];
        for (const file of files) {
            if (!/\.as$/i.test(file.path)) continue;
            try {
                const info = inspectScript(file.text);
                for (const item of info.constants) {
                    if (["int", "uint", "float", "double"].includes(item.type)) {
                        params.push({...item, path: file.path});
                    }
                }
            } catch (_) { /* Skip unreadable or invalid scripts during discovery. */ }
        }
        return params;
    }

    function readInitLayers(files) {
        const resolved = resolve(files, "script/init.as");
        if (!resolved) throw Error("This profile has no script/init.as to inspect.");
        const file = resolved.file;
        const {source, tokens, ends} = tokenizeSource(file.text);
        const aiInits = [];
        for (let i = 1; i + 3 < tokens.length; i++) {
            if (tokens[i].value === "AiInit" && tokens[i + 1].value === "(" &&
                ends.get(i + 1) === i + 2 && tokens[i + 3].value === "{") {
                aiInits.push({open: i + 3, close: ends.get(i + 3)});
            }
        }
        if (aiInits.length !== 1) throw Error("Could not locate a unique AiInit() function.");
        const fn = aiInits[0];
        const aliases = new Set();
        let baseMatch = null;
        const branches = [];
        const seenOptions = new Set();

        function extractStringList(openIdx, closeIdx, label) {
            const open = tokens[openIdx].offset;
            const close = tokens[closeIdx].offset + 1;
            if (/\/\/|\/\*/.test(file.text.slice(open, close))) {
                throw Error(`Cannot safely edit ${label} containing comments; use All files.`);
            }
            const items = [];
            let expectComma = false;
            for (let k = openIdx + 1; k < closeIdx; k++) {
                const val = tokens[k].value;
                if (expectComma) {
                    if (val !== ",") throw Error(`Unsupported entry in ${label}.`);
                    expectComma = false;
                } else {
                    if (!/^"[^"\\]*"$/.test(val)) throw Error(`Non-literal entry in ${label}.`);
                    items.push(val.slice(1, -1));
                    expectComma = true;
                }
            }
            return {items, open, close};
        }

        function statementEnd(index, maximum) {
            for (let i = index; i < maximum; i++) {
                if (tokens[i].value === ";") return i + 1;
                if (ends.has(i)) i = ends.get(i);
            }
            return maximum;
        }

        function scanBranchInsert(first, last, optionKey) {
            const found = [];
            for (let i = first; i < last;) {
                const val = tokens[i].value;
                if (val === "{") {
                    found.push(...scanBranchInsert(i + 1, ends.get(i), optionKey));
                    i = ends.get(i) + 1;
                    continue;
                }
                if (flowKeywords.has(val)) {
                    let next = i + 1;
                    if (tokens[next]?.value === "(") next = ends.get(next) + 1;
                    next = tokens[next]?.value === "{" ? ends.get(next) + 1 : statementEnd(next, last);
                    i = next;
                    continue;
                }
                const next = statementEnd(i, last);
                const textSlice = source.slice(tokens[i].offset, tokens[next - 1].offset + tokens[next - 1].value.length).trim();
                const insertMatch = textSlice.match(/^(\w+)\s*\.\s*profile\s*\.\s*insertAt\s*\(\s*\1\s*\.\s*profile\s*\.\s*length\s*\(\s*\)\s*,\s*\{([^{}]*)\}\s*\)\s*;$/);
                if (insertMatch) {
                    let braceIdx = -1;
                    for (let k = i; k < next; k++) {
                        if (tokens[k].value === "{") braceIdx = k;
                    }
                    if (braceIdx >= 0) found.push(extractStringList(braceIdx, ends.get(braceIdx), layerOptions.get(optionKey)));
                }
                i = next;
            }
            return found;
        }

        for (let i = fn.open + 1; i < fn.close;) {
            const val = tokens[i].value;
            if (val === "{") { i = ends.get(i) + 1; continue; }
            if (val === "if" && tokens[i + 1]?.value === "(") {
                const condClose = ends.get(i + 1);
                const condText = source.slice(tokens[i + 1].offset + 1, tokens[condClose].offset).trim();
                const known = modCondition(condText, aliases);
                const thenStart = condClose + 1;
                const thenEnd = tokens[thenStart]?.value === "{" ? ends.get(thenStart) + 1 : statementEnd(thenStart, fn.close);
                let after = thenEnd;
                let elseStart = -1, elseEnd = -1;
                if (tokens[thenEnd]?.value === "else") {
                    elseStart = thenEnd + 1;
                    elseEnd = tokens[elseStart]?.value === "{" ? ends.get(elseStart) + 1 : statementEnd(elseStart, fn.close);
                    after = elseEnd;
                }
                if (known && !seenOptions.has(known.key)) {
                    const targetStart = known.enabled ? thenStart : elseStart;
                    const targetEnd = known.enabled ? thenEnd : elseEnd;
                    if (targetStart >= 0) {
                        const inserts = scanBranchInsert(targetStart, targetEnd, known.key);
                        if (inserts.length === 1) {
                            seenOptions.add(known.key);
                            branches.push({
                                option: known.key,
                                label: layerOptions.get(known.key),
                                layers: inserts[0].items,
                                open: inserts[0].open,
                                close: inserts[0].close
                            });
                        }
                    }
                }
                i = after;
                continue;
            }
            if (flowKeywords.has(val)) {
                let body = i + 1;
                if (tokens[body]?.value === "(") body = ends.get(body) + 1;
                const next = tokens[body]?.value === "{" ? ends.get(body) + 1 : statementEnd(body, fn.close);
                i = val === "do" && tokens[next]?.value === "while" ? statementEnd(next, fn.close) : next;
                continue;
            }
            const next = statementEnd(i, fn.close);
            const stmt = source.slice(tokens[i].offset, tokens[next - 1].offset + tokens[next - 1].value.length).trim();
            const alias = stmt.match(/^dictionary\s*@?\s*(\w+)\s*=\s*aiSetupMgr\s*\.\s*GetModOptions\s*\(\s*\)\s*;$/);
            if (alias) aliases.add(alias[1]);
            const assign = stmt.match(/^@?\s*(\w+)\s*\.\s*profile\s*=\s*@\s*\(\s*array\s*<\s*string\s*>\s*=\s*\{([^{}]*)\}\s*\)\s*;$/);
            if (assign) {
                if (baseMatch) throw Error("Expected a single base profile layer assignment in AiInit().");
                let braceIdx = -1;
                for (let k = i; k < next; k++) {
                    if (tokens[k].value === "{") braceIdx = k;
                }
                if (braceIdx >= 0) {
                    const extracted = extractStringList(braceIdx, ends.get(braceIdx), "base profile layers");
                    baseMatch = {layers: extracted.items, open: extracted.open, close: extracted.close};
                }
            }
            i = next;
        }
        if (!baseMatch) throw Error("Could not find a statically editable base layer list in AiInit().");
        return {file, base: baseMatch.layers, baseSpan: {open: baseMatch.open, close: baseMatch.close}, branches};
    }

    function writeInitLayers(files, updated) {
        const current = readInitLayers(files);
        function normalizeLayerList(items, allowEmpty, label) {
            if (!Array.isArray(items)) throw Error(`Expected a list of layers for ${label}.`);
            if (!allowEmpty && !items.length) throw Error("At least one base configuration layer is required.");
            const seen = new Set();
            return items.map(raw => {
                if (typeof raw !== "string") throw Error(`Invalid layer name in ${label}.`);
                let name = raw.trim().replace(/\\/g, "/");
                if (name.startsWith("config/")) name = name.slice("config/".length);
                if (name.endsWith(".json")) name = name.slice(0, -".json".length);
                if (!/^(?:[\w-]+\/)*[\w-]+$/.test(name)) throw Error(`Unsupported layer filename: ${raw}`);
                if (seen.has(name)) throw Error(`Duplicate layer "${name}" in ${label}.`);
                seen.add(name);
                return name;
            });
        }
        const edits = [{
            open: current.baseSpan.open,
            close: current.baseSpan.close,
            text: "{" + normalizeLayerList(updated?.base, false, "base layers").map(n => JSON.stringify(n)).join(", ") + "}"
        }];
        if (Array.isArray(updated?.branches)) {
            for (const branch of current.branches) {
                const nextBranch = updated.branches.find(item => item.option === branch.option);
                if (!nextBranch) continue;
                edits.push({
                    open: branch.open,
                    close: branch.close,
                    text: "{" + normalizeLayerList(nextBranch.layers, true, branch.label).map(n => JSON.stringify(n)).join(", ") + "}"
                });
            }
        }
        edits.sort((a, b) => b.open - a.open);
        let result = current.file.text;
        for (const edit of edits) {
            result = result.slice(0, edit.open) + edit.text + result.slice(edit.close);
        }
        return result;
    }

    const recruitStart = "// BAR AI Studio recruit rules ";
    const recruitEnd = "// End BAR AI Studio recruit rules";
    const recruitHook = "// BAR AI Studio recruit hook";

    function normalizeRecruitRules(rules) {
        if (!Array.isArray(rules) || !rules.length) throw Error("Recruitment rules must be a nonempty list.");
        const seen = new Set();
        return rules.map(raw => {
            const factory = raw?.factory;
            const unit = raw?.unit;
            const target = raw?.target;
            const startMinutes = raw?.startMinutes ?? 0;
            const cooldownSeconds = raw?.cooldownSeconds ?? 0;
            const yieldNormal = Boolean(raw?.yieldNormal);
            const requireMetal = Boolean(raw?.requireMetal);
            const rawMilestones = raw?.milestones ?? [];
            const enemyRole = typeof raw?.enemyRole === "string" ? raw.enemyRole.trim() : "";
            const rawEnemyCost = raw?.enemyCost ?? 0;

            if (![factory, unit].every(id => typeof id === "string" && /^[a-z0-9_]+$/.test(id))) {
                throw Error("Enter a BAR unit ID for both factory and recruited unit.");
            }
            if (factory === unit) throw Error("Factory and recruited unit must differ.");
            if (!Number.isInteger(target) || target < 1 || target > 1000) {
                throw Error("Use a whole-number target count from 1 to 1000.");
            }
            if (!Number.isInteger(startMinutes) || startMinutes < 0 || startMinutes > 1000) {
                throw Error("Use a whole-number start delay from 0 to 1000 minutes.");
            }
            if (!Number.isInteger(cooldownSeconds) || cooldownSeconds < 0 || cooldownSeconds > 3600) {
                throw Error("Use a whole-number cooldown from 0 to 3600 seconds.");
            }
            if (!Array.isArray(rawMilestones)) throw Error("Milestone buildings must be a list of BAR unit IDs.");
            const seenMilestones = new Set();
            const milestones = rawMilestones.map(id => {
                if (typeof id !== "string" || !/^[a-z0-9_]+$/.test(id)) {
                    throw Error("Enter valid BAR unit IDs for milestone buildings.");
                }
                if (seenMilestones.has(id)) throw Error("Duplicate milestone building: " + id);
                seenMilestones.add(id);
                return id;
            });
            if (enemyRole && !/^[a-z0-9_]+$/.test(enemyRole)) {
                throw Error("Enter a valid enemy role name or leave it empty.");
            }
            const enemyCost = enemyRole ? Number(rawEnemyCost) : 0;
            if (!Number.isFinite(enemyCost) || enemyCost < 0) {
                throw Error("Use a non-negative finite enemy metal cost threshold.");
            }
            const rule = {
                factory, unit, target, startMinutes, cooldownSeconds,
                yieldNormal, requireMetal, milestones, enemyRole, enemyCost
            };
            const key = JSON.stringify(rule);
            if (seen.has(key)) throw Error("Duplicate recruitment rule.");
            seen.add(key);
            return rule;
        });
    }

    function recruitBlock(rules, newline) {
        const entries = rules.map(rule => {
            const milestoneLiteral = rule.milestones.length
                ? `{${rule.milestones.map(id => JSON.stringify(id)).join(", ")}}`
                : "array<string>()";
            const costLiteral = Number.isInteger(rule.enemyCost) ? `${rule.enemyCost}.f` : `${rule.enemyCost}f`;
            return `    RecruitRule(${JSON.stringify(rule.factory)}, ${JSON.stringify(rule.unit)}, ${rule.target}, ${rule.startMinutes * 1800}, ${rule.cooldownSeconds * 30}, ${rule.yieldNormal}, ${rule.requireMetal}, ${milestoneLiteral}, ${JSON.stringify(rule.enemyRole)}, ${costLiteral})`;
        });
        return `\n${recruitStart}${JSON.stringify(rules)}
namespace StudioRecruit {

class RecruitRule {
    string factory;
    string unit;
    int target;
    int startFrame;
    int cooldownFrames;
    bool yieldNormal;
    bool requireMetal;
    array<string> milestones;
    array<bool> milestoneSeen;
    string enemyRole;
    float enemyCost;
    int nextOrder;
    bool yieldTurn;
    IUnitTask@ pending;

    RecruitRule(const string& in factoryName, const string& in unitName, int targetCount,
                int minFrame, int cooldown, bool alternateTurn, bool checkMetal,
                const array<string>& in watchedMilestones, const string& in roleName, float minEnemyCost) {
        factory = factoryName;
        unit = unitName;
        target = targetCount;
        startFrame = minFrame;
        cooldownFrames = cooldown;
        yieldNormal = alternateTurn;
        requireMetal = checkMetal;
        milestones = watchedMilestones;
        milestoneSeen.resize(milestones.length());
        for (uint i = 0; i < milestoneSeen.length(); i++) {
            milestoneSeen[i] = false;
        }
        enemyRole = roleName;
        enemyCost = minEnemyCost;
        nextOrder = 0;
        yieldTurn = false;
    }

    void UpdateMilestones() {
        for (uint i = 0; i < milestones.length(); i++) {
            if (!milestoneSeen[i]) {
                CCircuitDef@ req = ai.GetCircuitDef(milestones[i]);
                if (req !is null && req.count > 0) {
                    milestoneSeen[i] = true;
                }
            }
        }
    }

    IUnitTask@ Try(CCircuitUnit@ plant) {
        UpdateMilestones();
        if (plant.circuitDef.GetName() != factory || pending !is null ||
            ai.frame < startFrame || ai.frame < nextOrder) {
            return null;
        }
        for (uint i = 0; i < milestoneSeen.length(); i++) {
            if (!milestoneSeen[i]) return null;
        }
        if (enemyRole != "" && aiEnemyMgr.GetEnemyCost(aiRoleMasker.GetTypeMask(enemyRole).type) < enemyCost) {
            return null;
        }
        CCircuitDef@ build = ai.GetCircuitDef(unit);
        if (build is null || build.count >= target || !build.IsAvailable(ai.frame)) {
            return null;
        }
        if (requireMetal && aiEconomyMgr.metal.current < build.costM) {
            return null;
        }
        if (yieldTurn) {
            yieldTurn = false;
            return null;
        }
        @pending = aiFactoryMgr.Enqueue(TaskS::Recruit(
            Task::RecruitType::FIREPOWER, Task::Priority::NORMAL,
            build, plant.GetPos(ai.frame), 64.f));
        if (pending !is null) {
            nextOrder = ai.frame + cooldownFrames;
            yieldTurn = yieldNormal;
            AiLog("Studio recruit: queued " + unit + ", count " + build.count);
        }
        return pending;
    }

    void Release(IUnitTask@ task) {
        if (task is pending) {
            @pending = null;
        }
    }
}

array<RecruitRule@> rules = {
${entries.join(",\n")}
};

IUnitTask@ TryRecruit(CCircuitUnit@ unit) {
    for (uint i = 0; i < rules.length(); i++) {
        IUnitTask@ task = rules[i].Try(unit);
        if (task !is null) return task;
    }
    return null;
}

void Release(IUnitTask@ task) {
    for (uint i = 0; i < rules.length(); i++) {
        rules[i].Release(task);
    }
}

}
${recruitEnd}\n`.replace(/\n/g, newline);
    }

    function makeHookSnippet(param, newline) {
        return `${newline}\tIUnitTask@ _sr = StudioRecruit::TryRecruit(${param}); if (_sr !is null) return _sr; ${recruitHook}`;
    }

    function removedHookSnippet(param, newline) {
        return `${newline}\tStudioRecruit::Release(${param}); ${recruitHook}`;
    }

    function findFactoryHooks(text) {
        const {tokens, ends} = tokenizeSource(text);
        const makeMatches = [], removedMatches = [];
        for (let i = 1; i + 3 < tokens.length; i++) {
            const name = tokens[i].value;
            if ((name !== "AiMakeTask" && name !== "AiTaskRemoved") ||
                tokens[i + 1].value !== "(" || [".", "::"].includes(tokens[i - 1].value)) continue;
            const parenClose = ends.get(i + 1);
            const braceOpen = parenClose + 1;
            if (tokens[braceOpen]?.value !== "{") continue;
            if (name === "AiMakeTask") {
                const paramTok = tokens[parenClose - 1];
                if (parenClose > i + 2 && isIdent(paramTok)) {
                    makeMatches.push({param: paramTok.value, brace: tokens[braceOpen].offset});
                }
            } else {
                let comma = -1;
                for (let k = i + 2; k < parenClose; k++) {
                    if (tokens[k].value === ",") { comma = k; break; }
                }
                const paramTok = comma > i + 2 ? tokens[comma - 1] : null;
                if (isIdent(paramTok)) {
                    removedMatches.push({param: paramTok.value, brace: tokens[braceOpen].offset});
                }
            }
            i = ends.get(braceOpen);
        }
        if (makeMatches.length !== 1 || removedMatches.length !== 1) {
            throw Error("This profile needs unique AiMakeTask() and AiTaskRemoved() functions in factory.as to use recruitment rules.");
        }
        return {
            makeParam: makeMatches[0].param,
            makeBrace: makeMatches[0].brace,
            removedParam: removedMatches[0].param,
            removedBrace: removedMatches[0].brace
        };
    }

    function readRecruitRules(files) {
        const path = "script/manager/factory.as";
        const resolved = resolve(files, path);
        const source = resolve(files, path, {all: true});
        if (!resolved || !source?.files.length) {
            throw Error("This profile needs a factory.as script to use recruitment rules.");
        }
        const issue = source.issues.find(item => item.code !== "include-cycle");
        if (issue) throw Error(issue.message);
        const file = resolved.file;
        const newline = file.text.includes("\r\n") ? "\r\n" : "\n";
        const start = file.text.indexOf(recruitStart);
        const end = file.text.indexOf(recruitEnd);
        const hasHook = file.text.includes(recruitHook);
        let original = file.text;
        let rules = [];
        if (start !== -1 || end !== -1 || hasHook) {
            try {
                if (start === -1 || end === -1) throw Error();
                const metadataEnd = file.text.indexOf(newline, start);
                if (metadataEnd === -1) throw Error();
                rules = normalizeRecruitRules(JSON.parse(file.text.slice(start + recruitStart.length, metadataEnd)));
                const block = recruitBlock(rules, newline);
                const blockStart = start - newline.length;
                if (blockStart < 0 || file.text.slice(blockStart, blockStart + block.length) !== block) throw Error();
                const withoutBlock = file.text.slice(0, blockStart) + file.text.slice(blockStart + block.length);
                const hooks = findFactoryHooks(withoutBlock);
                const makeHook = makeHookSnippet(hooks.makeParam, newline);
                const removedHook = removedHookSnippet(hooks.removedParam, newline);
                if (withoutBlock.slice(hooks.makeBrace + 1, hooks.makeBrace + 1 + makeHook.length) !== makeHook) throw Error();
                if (withoutBlock.slice(hooks.removedBrace + 1, hooks.removedBrace + 1 + removedHook.length) !== removedHook) throw Error();
                const strips = [
                    {start: hooks.makeBrace + 1, end: hooks.makeBrace + 1 + makeHook.length},
                    {start: hooks.removedBrace + 1, end: hooks.removedBrace + 1 + removedHook.length}
                ].sort((a, b) => b.start - a.start);
                original = withoutBlock;
                for (const strip of strips) original = original.slice(0, strip.start) + original.slice(strip.end);
                if (original.includes(recruitHook) || original.includes(recruitStart) || original.includes(recruitEnd)) throw Error();
            } catch {
                throw Error("The generated recruitment rules script was edited. Restore its generated block and hooks to resume visual editing.");
            }
        }
        for (const candidate of source.files) {
            if (/\bStudioRecruit\b/.test(mask(candidate === file ? original : candidate.text))) {
                throw Error("This profile already defines a custom StudioRecruit namespace.");
            }
        }
        findFactoryHooks(original);
        return {file, original, newline, rules};
    }

    function writeRecruitRules(files, rules) {
        const {original, newline} = readRecruitRules(files);
        if (!rules.length) return original;
        const normalized = normalizeRecruitRules(rules);
        const hooks = findFactoryHooks(original);
        const inserts = [
            {pos: hooks.makeBrace + 1, text: makeHookSnippet(hooks.makeParam, newline)},
            {pos: hooks.removedBrace + 1, text: removedHookSnippet(hooks.removedParam, newline)}
        ].sort((a, b) => b.pos - a.pos);
        let updated = original;
        for (const insert of inserts) {
            updated = updated.slice(0, insert.pos) + insert.text + updated.slice(insert.pos);
        }
        return updated + recruitBlock(normalized, newline);
    }

    return {
        mask, resolve, layerPresets, readConstructionRules, writeConstructionRules,
        inspectScript, patchScriptLiteral, discoverScriptParameters,
        readInitLayers, writeInitLayers, readRecruitRules, writeRecruitRules
    };
})();
