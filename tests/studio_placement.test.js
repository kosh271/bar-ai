"use strict";

const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

global.window = {};
require("../web/studio/config.js");
const C = window.StudioConfig;
const studio = readFileSync(join(__dirname, "../web/studio/studio.js"), "utf8");
const placement = studio.slice(studio.indexOf("function placementLaneControl"), studio.indexOf("function drawPlacementPreview"));

function laneControl(rule) {
    const file = {text: `{"building":{"class_land":{"fac_air":${rule}}}}`};
    const path = ["building", "class_land", "fac_air"];
    const element = (_tag, _css, text) => ({
        text, items: [], append(...items) {this.items.push(...items);},
        replaceChildren(...items) {this.items = items;},
    });
    const panel = element("div");
    let rendered = false;
    const context = {
        C, file, panel, element, path,
        parse: current => C.parse(current.text),
        button: (text, click) => ({text, click}),
        assign: (current, text) => {current.text = text;},
        render: () => {rendered = true;},
        message: text => {if (text) assert.fail(text);},
    };
    vm.runInNewContext(`${placement}\nplacementLaneControl(panel, file, path);`, context);
    return {file, path, controls: panel.items, didRender: () => rendered};
}

test("adds the lane category to the selected rule and preserves its existing categories and comments", () => {
    const {file, path, controls, didRender} = laneControl('{"not_ignore":["factory", /* existing */ "special"]}');
    const action = controls.find(item => item.text === "Include reserved lanes");
    action.click();
    assert.deepEqual(C.at(C.parse(file.text), [...path, "not_ignore"]).children.map(({node}) => node.value),
        ["factory", "special", "terra"]);
    assert.match(file.text, /\/\* existing \*\//);
    assert.equal(didRender(), true);
});

test("only offers the edit for an active restrictive list that omits the lane category", () => {
    for (const rule of ['{}', '{"ignore":["all"],"not_ignore":["factory"]}',
        '{"not_ignore":["factory","terra"]}', '{"not_ignore":["all"]}', '{"not_ignore":[]}']) {
        const {controls} = laneControl(rule);
        assert.equal(controls.some(item => item.click), false, rule);
    }
});
