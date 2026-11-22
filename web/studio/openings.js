(function () {
  "use strict";

  // mount(container, { text, onChange }) emits the complete source; destroy()
  // removes the UI. Only literal dictionary keys can be added without external
  // Factory symbol definitions, which this standalone editor does not receive.
  // This is deliberately a small recognizer, not an AngelScript parser.
  const roles = [
    "BUILDER", "BUILDER2", "SCOUT", "RAIDER", "RIOT", "ASSAULT",
    "SKIRM", "ARTY", "AA", "AS", "AH", "AHA", "BOMBER", "SUPPORT",
    "MINE", "TRANS", "AIR", "SUB", "STATIC", "HEAVY", "SUPER", "COMM"
  ];

  function lex(text, editable = true) {
    const tokens = [];
    let i = 0;
    while (i < text.length) {
      const start = i;
      const c = text[i];
      if (/\s/.test(c)) { i++; continue; }
      if (text.slice(i, i + 2) === "//") {
        i += 2;
        while (i < text.length && text[i] !== "\n" && text[i] !== "\r") i++;
        continue;
      }
      if (text.slice(i, i + 2) === "/*") {
        const end = text.indexOf("*/", i + 2);
        if (end < 0) throw Error("Unterminated block comment.");
        i = end + 2;
        continue;
      }
      if (c === '"' || c === "'") {
        i++;
        while (i < text.length && text[i] !== c) {
          if (text[i] === "\\") i++;
          if (i >= text.length || text[i] === "\n" || text[i] === "\r") {
            throw Error("Unterminated string.");
          }
          i++;
        }
        if (i >= text.length) throw Error("Unterminated string.");
        i++;
      } else {
        const match = text.slice(i).match(/^(?:[A-Za-z_]\w*|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fF]?|::|.)/);
        i += match[0].length;
      }
      tokens.push({ value: text.slice(start, i), start: start, end: i });
    }
    if (editable) tokens.forEach(function (token, index) {
      if (token.value === "#" && (!tokens[index + 1] || tokens[index + 1].value !== "include")) {
        throw Error("Preprocessor directives other than #include are not supported.");
      }
    });
    return tokens;
  }

  function roleReferences(text) {
    const tokens = lex(text, false), references = [];
    for (let i = 0; i + 2 < tokens.length; i++) {
      if (tokens[i].value === "RT" && tokens[i + 1].value === "::" && /^[A-Za-z_]\w*$/.test(tokens[i + 2].value)) {
        references.push({name: tokens[i + 2].value, offset: tokens[i].start});
      }
    }
    return references;
  }

  function balanced(tokens, start) {
    const pairs = { "{": "}", "(": ")", "[": "]" };
    const stack = [];
    for (let i = start; i < tokens.length; i++) {
      const v = tokens[i].value;
      if (pairs[v]) stack.push(pairs[v]);
      else if (v === "}" || v === ")" || v === "]") {
        if (stack.pop() !== v) throw Error("Unbalanced delimiters.");
        if (!stack.length) return i;
      }
    }
    throw Error("Unclosed delimiter.");
  }

  function list(tokens, open, close) {
    const items = [];
    let start = open + 1;
    let comma = null;
    for (let i = start; i < close; i++) {
      const v = tokens[i].value;
      if (v === "{" || v === "(" || v === "[") {
        i = balanced(tokens, i);
        if (i >= close) throw Error("Invalid list delimiters.");
      } else if (v === "}" || v === ")" || v === "]") {
        throw Error("Invalid list delimiters.");
      } else if (v === ",") {
        if (i === start) throw Error("Empty list entry.");
        items.push({ first: start, last: i - 1, before: comma, after: i });
        comma = i;
        start = i + 1;
      }
    }
    if (start < close) items.push({ first: start, last: close - 1, before: comma, after: null });
    return items;
  }

  function expect(t, i, value) {
    if (!t[i] || t[i].value !== value) throw Error("Expected " + value + ".");
    return i + 1;
  }

  function parseStep(t, item) {
    let i = item.first;
    i = expect(t, i, "SO");
    i = expect(t, i, "(");
    i = expect(t, i, "RT");
    i = expect(t, i, "::");
    if (!t[i] || !/^[A-Za-z_]\w*$/.test(t[i].value)) throw Error("Expected a named RT role.");
    const role = t[i++];
    let count = null;
    if (t[i] && t[i].value === ",") {
      i++;
      count = t[i++];
      if (!count || !/^\d+$/.test(count.value) || Number(count.value) < 1 ||
          Number(count.value) > 4294967295) throw Error("Step count must be a positive uint literal.");
    }
    i = expect(t, i, ")");
    if (i !== item.last + 1) throw Error("Unsupported step expression.");
    return { item: item, role: role, count: count };
  }

  function parseQueue(t, item) {
    let i = item.first;
    i = expect(t, i, "SQueue");
    i = expect(t, i, "(");
    const weight = t[i++];
    if (!weight || !/^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fF]?$/.test(weight.value) ||
        !Number.isFinite(Number(weight.value.replace(/[fF]$/, ""))) ||
        Number(weight.value.replace(/[fF]$/, "")) > 3.402823466e38) {
      throw Error("Queue weight must be a non-negative float literal.");
    }
    i = expect(t, i, ",");
    i = expect(t, i, "{");
    const open = i - 1;
    const close = balanced(t, open);
    const steps = list(t, open, close).map(function (entry) { return parseStep(t, entry); });
    i = expect(t, close + 1, ")");
    if (i !== item.last + 1) throw Error("Unsupported queue expression.");
    return { item: item, weight: weight, open: open, close: close, steps: steps };
  }

  function parseFactory(t, item) {
    let i = item.first;
    i = expect(t, i, "{");
    let key;
    let literal = false;
    if (t[i] && /^"[A-Za-z_]\w*"$/.test(t[i].value)) {
      key = t[i++].value.slice(1, -1);
      literal = true;
    } else {
      i = expect(t, i, "Factory");
      i = expect(t, i, "::");
      if (!t[i] || !/^[A-Za-z_]\w*$/.test(t[i].value)) throw Error("Unrecognized factory key.");
      key = t[i++].value;
    }
    const factory = { name: key, literal: literal, item: item, error: null };
    try {
      i = expect(t, i, ",");
      i = expect(t, i, "array");
      i = expect(t, i, "<");
      i = expect(t, i, "SQueue");
      i = expect(t, i, ">");
      i = expect(t, i, "=");
      i = expect(t, i, "{");
      factory.open = i - 1;
      factory.close = balanced(t, factory.open);
      factory.queues = list(t, factory.open, factory.close).map(function (entry) {
        return parseQueue(t, entry);
      });
      if (!factory.queues.length) throw Error("A factory needs at least one queue.");
      i = expect(t, factory.close + 1, "}");
      if (i !== item.last + 1) throw Error("Unsupported factory expression.");
    } catch (error) {
      factory.error = error.message;
    }
    return factory;
  }

  function parse(text) {
    const t = lex(text);
    const matches = [];
    for (let i = 0; i + 3 < t.length; i++) {
      if (t[i].value === "GetOpenInfo" && t[i + 1].value === "(" &&
          t[i + 2].value === ")" && t[i + 3].value === "{") matches.push(i);
    }
    if (matches.length !== 1) throw Error("Expected exactly one GetOpenInfo() definition.");
    const m = matches[0];
    if (m < 2 || t[m - 2].value !== "SOpener" || t[m - 1].value !== "@") {
      throw Error("Unrecognized GetOpenInfo return type.");
    }
    let i = m + 4;
    i = expect(t, i, "return");
    i = expect(t, i, "SOpener");
    i = expect(t, i, "(");
    i = expect(t, i, "{");
    const open = i - 1;
    const close = balanced(t, open);
    const entries = list(t, open, close);
    const factories = entries.map(function (entry) { return parseFactory(t, entry); });
    if (factories.some(function (factory) { return factory.literal; }) &&
        factories.some(function (factory) { return !factory.literal; })) {
      throw Error("Mixed literal and Factory:: keys may refer to the same factory.");
    }
    const names = new Set();
    factories.forEach(function (factory) {
      if (names.has(factory.name)) throw Error("Duplicate factory key: " + factory.name + ".");
      names.add(factory.name);
    });
    i = expect(t, close + 1, ",");
    i = expect(t, i, "{");
    const defaultClose = balanced(t, i - 1);
    list(t, i - 1, defaultClose).forEach(function (entry) { parseStep(t, entry); });
    i = expect(t, defaultClose + 1, ")");
    i = expect(t, i, ";");
    expect(t, i, "}");
    return { tokens: t, open: open, close: close, entries: entries, factories: factories };
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function mount(container, options) {
    options = options || {};
    let text = typeof options.text === "string" ? options.text : null;
    let selected = null;
    const canChange = typeof options.onChange === "function";
    let disposed = false;

    function patch(changes) {
      if (!canChange || disposed) return;
      changes.sort(function (a, b) { return b.start - a.start; });
      let next = text;
      changes.forEach(function (change) {
        next = next.slice(0, change.start) + change.text + next.slice(change.end);
      });
      if (next === text) return;
      try {
        const before = parse(text);
        const after = parse(next);
        before.factories.forEach(function (factory) {
          const updated = after.factories.find(function (f) { return f.name === factory.name; });
          if (!factory.error && (!updated || updated.error)) throw Error("The edit would make a factory unrecognizable.");
        });
      } catch (error) {
        notice("Edit was not applied: " + error.message);
        return;
      }
      text = next;
      options.onChange(text);
      render();
    }

    function notice(message) {
      const node = element("p", "notice", message);
      node.setAttribute("role", "status");
      container.appendChild(node);
    }

    function button(parent, label, action, disabled) {
      const b = element("button", "", label);
      b.type = "button";
      b.disabled = !canChange || !!disabled;
      b.addEventListener("click", action);
      parent.appendChild(b);
      return b;
    }

    function field(parent, label, input) {
      const wrap = element("label", "opening-field");
      wrap.appendChild(element("span", "", label));
      wrap.appendChild(input);
      parent.appendChild(wrap);
    }

    function remove(t, items, index) {
      const item = items[index];
      const changes = [{ start: t[item.first].start, end: t[item.last].end, text: "" }];
      const comma = item.after !== null ? item.after : item.before;
      if (comma !== null) changes.push({ start: t[comma].start, end: t[comma].end, text: "" });
      patch(changes);
    }

    function hasComments(start, end) {
      return /\/\/|\/\*/.test(text.slice(start, end));
    }

    function move(t, items, index, direction) {
      const a = items[index];
      const b = items[index + direction];
      if (!b) return;
      const first = direction > 0 ? a : b;
      const last = direction > 0 ? b : a;
      if (hasComments(t[first.last].end, t[last.first].start)) {
        notice("Cannot reorder across comments safely; use the source view.");
        return;
      }
      patch([
        { start: t[a.first].start, end: t[a.last].end, text: text.slice(t[b.first].start, t[b.last].end) },
        { start: t[b.first].start, end: t[b.last].end, text: text.slice(t[a.first].start, t[a.last].end) }
      ]);
    }

    function hasLeadingComment(t, open, items, index) {
      const start = index ? t[items[index - 1].last].end : t[open].end;
      return hasComments(start, t[items[index].first].start);
    }

    function add(t, open, items, value, separator) {
      if (items.length) {
        const last = items[items.length - 1];
        const pos = t[last.last].end;
        // A trailing comma already separates the new entry.
        if (last.after !== null) {
          const commaEnd = t[last.after].end;
          patch([{ start: commaEnd, end: commaEnd, text: separator + value }]);
        } else {
          patch([{ start: pos, end: pos, text: "," + separator + value }]);
        }
      } else {
        const pos = t[open].end;
        patch([{ start: pos, end: pos, text: value }]);
      }
    }

    function render() {
      if (disposed) return;
      while (container.firstChild) container.removeChild(container.firstChild);
      container.appendChild(element("h2", "", "Factory openers"));
      if (text === null) {
        notice("No commander.as text was supplied. Open a source file to use the visual editor.");
        return;
      }
      let model;
      try { model = parse(text); } catch (error) {
        notice("Visual editing unavailable: " + error.message + " Use the source view for this script.");
        return;
      }
      if (!canChange) notice("Read-only: no onChange callback was supplied.");
      container.appendChild(element("p", "muted", "Weights are relative and are never normalized. The default opener is preserved in source."));
      const t = model.tokens;
      const select = element("select");
      if (!model.factories.some(function (f) { return f.name === selected; })) {
        selected = model.factories.length ? model.factories[0].name : null;
      }
      model.factories.forEach(function (f) {
        const key = f.literal ? '"' + f.name + '"' : "Factory::" + f.name;
        const name = typeof options.unitName === "function" ? options.unitName(f.name) : "";
        const label = name ? name + (f.literal ? " · " : " (name match) · ") + key : key;
        const option = element("option", "", label + (f.error ? " — unsupported" : ""));
        option.value = f.name;
        select.appendChild(option);
      });
      select.value = selected || "";
      select.disabled = !model.factories.length;
      select.addEventListener("change", function () { selected = select.value; render(); });
      field(container, "Factory", select);

      const factory = model.factories.find(function (f) { return f.name === selected; });
      if (factory && typeof options.unitIcon === "function") container.appendChild(options.unitIcon(factory.name));
      if (factory && factory.error) {
        notice("This factory is read-only: " + factory.error + " Its source is preserved; use the source view.");
      } else if (factory) {
        const weights = [], hints = [];
        function refreshShares() {
          const helper = window.StudioEditors;
          const shares = helper.weightShares(weights.map(function (input) { return input.valueAsNumber; }));
          hints.forEach(function (hint, index) { hint.textContent = helper.weightPercent(shares[index]); });
        }
        factory.queues.forEach(function (queue, qi) {
          const panel = element("section", "panel opening-queue");
          const heading = element("h3", "", "Queue " + (qi + 1));
          const hint = element("span", "weight-share opening-weight-share");
          hint.id = "opening-share-" + qi;
          heading.appendChild(hint);
          panel.appendChild(heading);
          const top = element("div", "row");
          const weight = element("input");
          weight.type = "number"; weight.min = "0"; weight.step = "any";
          const originalWeight = queue.weight.value.replace(/[fF]$/, "").replace(/\.(?=[eE]|$)/, ".0");
          weight.value = originalWeight;
          weight.disabled = !canChange;
          weight.setAttribute("aria-describedby", hint.id);
          weights.push(weight);
          hints.push(hint);
          weight.addEventListener("input", refreshShares);
          weight.addEventListener("change", function () {
            const value = weight.value.trim();
            if (!/^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) ||
                !Number.isFinite(Number(value)) || Number(value) > 3.402823466e38) {
              notice("Enter a non-negative finite float weight.");
              weight.value = originalWeight;
              refreshShares();
              return;
            }
            patch([{ start: queue.weight.start, end: queue.weight.end,
              text: (/^\d+$/.test(value) ? value + ".0" : value) + "f" }]);
          });
          field(top, "Relative weight", weight);
          function moveQueue(direction) {
            const items = factory.queues.map(function (q) { return q.item; });
            if (hasLeadingComment(t, factory.open, items, qi) ||
                hasLeadingComment(t, factory.open, items, qi + direction)) {
              notice("Cannot reorder queues with adjacent comments safely; use the source view.");
              return;
            }
            move(t, items, qi, direction);
          }
          button(top, "Move queue up", function () { moveQueue(-1); }, qi === 0);
          button(top, "Move queue down", function () { moveQueue(1); }, qi === factory.queues.length - 1);
          button(top, "Remove queue", function () {
            const items = factory.queues.map(function (q) { return q.item; });
            if (hasLeadingComment(t, factory.open, items, qi) ||
                hasComments(t[queue.item.first].start, t[queue.item.last].end)) {
              notice("Cannot remove a queue with attached comments; use the source view.");
              return;
            }
            remove(t, items, qi);
          }, factory.queues.length === 1);
          panel.appendChild(top);
          const steps = element("ol", "opening-steps");
          queue.steps.forEach(function (step, si) {
            const row = element("li", "row opening-step");
            const role = element("select");
            const choices = roles.indexOf(step.role.value) < 0 ? [step.role.value].concat(roles) : roles;
            choices.forEach(function (name) {
              const option = element("option", "", "RT::" + name);
              option.value = name;
              role.appendChild(option);
            });
            role.value = step.role.value; role.disabled = !canChange;
            role.addEventListener("change", function () {
              if (choices.indexOf(role.value) < 0) return;
              patch([{ start: step.role.start, end: step.role.end, text: role.value }]);
            });
            field(row, "Role", role);
            const count = element("input");
            count.type = "number"; count.min = "1"; count.max = "4294967295"; count.step = "1";
            count.value = step.count ? step.count.value : "1"; count.disabled = !canChange;
            count.addEventListener("change", function () {
              const value = count.value.trim();
              if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 4294967295) {
                notice("Enter a whole step count between 1 and 4294967295.");
                count.value = step.count ? step.count.value : "1";
                return;
              }
              if (step.count) patch([{ start: step.count.start, end: step.count.end, text: value }]);
              else if (Number(value) !== 1) patch([{ start: step.role.end, end: step.role.end, text: ", " + value }]);
            });
            field(row, "Count", count);
            function moveStep(direction) {
              const items = queue.steps.map(function (s) { return s.item; });
              if (hasLeadingComment(t, queue.open, items, si) ||
                  hasLeadingComment(t, queue.open, items, si + direction)) {
                notice("Cannot reorder steps with adjacent comments safely; use the source view.");
                return;
              }
              move(t, items, si, direction);
            }
            button(row, "Move step up", function () { moveStep(-1); }, si === 0);
            button(row, "Move step down", function () { moveStep(1); }, si === queue.steps.length - 1);
            button(row, "Remove step", function () {
              const items = queue.steps.map(function (s) { return s.item; });
              if (hasLeadingComment(t, queue.open, items, si) ||
                  hasComments(t[step.item.first].start, t[step.item.last].end)) {
                notice("Cannot remove a step with attached comments; use the source view.");
                return;
              }
              remove(t, items, si);
            });
            steps.appendChild(row);
          });
          panel.appendChild(steps);
          button(panel, "Add step", function () {
            add(t, queue.open, queue.steps.map(function (s) { return s.item; }), "SO(RT::BUILDER)", " ");
          });
          container.appendChild(panel);
        });
        refreshShares();
        button(container, "Add queue", function () {
          const newline = text.indexOf("\r\n") >= 0 ? "\r\n" : "\n";
          const lineStart = text.lastIndexOf("\n", t[factory.item.first].start - 1) + 1;
          const indent = (text.slice(lineStart, t[factory.item.first].start).match(/^\s*/) || [""])[0] + "\t";
          add(t, factory.open, factory.queues.map(function (q) { return q.item; }),
            "SQueue(1.0f, {SO(RT::BUILDER)})", newline + indent);
        });
      }
      if (model.factories.every(function (f) { return f.literal; })) {
        const row = element("div", "row");
        const name = element("input");
        name.type = "text"; name.placeholder = "Factory unit name"; name.disabled = !canChange;
        field(row, "New factory", name);
        button(row, "Add factory", function () {
          const value = name.value.trim();
          if (!/^[A-Za-z_]\w*$/.test(value) || model.factories.some(function (f) { return f.name === value; })) {
            notice("Enter a unique factory unit name using letters, digits and underscores.");
            return;
          }
          selected = value;
          const newline = text.indexOf("\r\n") >= 0 ? "\r\n" : "\n";
          add(t, model.open, model.entries, '{"' + value + '", array<SQueue> = {SQueue(1.0f, {SO(RT::BUILDER)})}}', newline + "\t\t");
        });
        container.appendChild(row);
      } else {
        container.appendChild(element("p", "muted", "Adding factories is unavailable: Factory:: keys may alias names not declared in this file. Use the source view."));
      }
    }
    render();
    return { destroy: function () { disposed = true; while (container.firstChild) container.removeChild(container.firstChild); } };
  }

  window.StudioOpenings = { mount: mount, roleReferences: roleReferences };
}());
