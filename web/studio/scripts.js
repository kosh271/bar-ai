"use strict";

window.StudioScripts = (() => {
    function mask(text) {
        const chars = text.split("");
        let i = 0;
        while (i < text.length) {
            if (text[i] === '"' || text[i] === "'") {
                const quote = text[i]; chars[i++] = " ";
                let closed = false;
                while (i < text.length) {
                    if (text[i] === "\\") {
                        chars[i++] = " ";
                        if (i < text.length) chars[i++] = " ";
                    } else if (text[i] === quote) { chars[i++] = " "; closed = true; break; }
                    else { if (!"\r\n".includes(text[i])) chars[i] = " "; i++; }
                }
                if (!closed) throw Error("Unterminated script string.");
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

    function resolve(files, path) {
        const find = name => files.find(file => file.path.replace(/\\/g, "/") === name);
        let file = find(path);
        if (!file) return null;
        const chain = [];
        while (true) {
            const current = file.path.replace(/\\/g, "/");
            if (chain.includes(current)) throw Error("Script include cycle: " + chain.concat(current).join(" → "));
            chain.push(current);
            const clean = mask(file.text);
            const pattern = /^[ \t]*#[ \t]*include[ \t]*"([^"\r\n]+)"[ \t]*\r?$/gm;
            const includes = [...file.text.matchAll(pattern)].filter(item => clean[item.index + item[0].indexOf("#")] === "#");
            if (includes.length !== 1) return {file, chain};
            const include = includes[0];
            if (clean.slice(0, include.index).trim() || clean.slice(include.index + include[0].length).trim()) return {file, chain};
            const name = include[1];
            if (name.includes("\\") || name.startsWith("/") || name.includes(":")) throw Error("Unsupported script include in " + current + ": " + name);
            const parts = current.split("/").slice(0, -1);
            for (const part of name.split("/")) {
                if (!part || part === ".") continue;
                if (part === "..") {
                    if (parts.length <= 1) throw Error("Script include is outside the profile script folder: " + name);
                    parts.pop();
                } else parts.push(part);
            }
            const next = parts.join("/");
            file = find(next);
            if (!file) throw Error("Included script not found: " + next);
        }
    }
    return {mask, resolve};
})();
