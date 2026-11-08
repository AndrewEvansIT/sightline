// Tests for the layout engine (web/grid.js). Run: node --test tests/
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const G = require("../web/grid.js");

const noOverlaps = (layout) => {
  for (let i = 0; i < layout.length; i++)
    for (let j = i + 1; j < layout.length; j++)
      assert.ok(!G.overlaps(layout[i], layout[j]), `${layout[i].id} overlaps ${layout[j].id}`);
};
const inBounds = (layout, u) => layout.forEach((b) => {
  assert.ok(b.x >= 0 && b.y >= 0, `${b.id} is off the grid`);
  if (u.mode === "v") assert.ok(b.x + b.w <= u.cols, `${b.id} is wider than the grid`);
  else assert.ok(b.y + b.h <= u.rows, `${b.id} is taller than the strip`);
});
const byId = (layout) => Object.fromEntries(layout.map((b) => [b.id, b]));

test("grid size follows the board", () => {
  const win = G.units(1200, 800, "window", "comfortable");
  assert.equal(win.mode, "v");
  assert.equal(win.cols, 12);
  const col = G.units(440, 1000, "column", "comfortable");
  assert.equal(col.cols, 4);
  const strip = G.units(3000, 500, "strip", "comfortable");
  assert.equal(strip.mode, "h");
  assert.equal(strip.rows, 10);
  assert.notEqual(G.profileKey(win), G.profileKey(col));
});

test("toPx and sizes round-trip on the grid", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const px = G.toPx({ x: 2, y: 3, w: 4, h: 5 }, u);
  assert.equal(px.top, 3 * (u.rowH + u.gap));
  assert.equal(px.height, 5 * u.rowH + 4 * u.gap);
});

test("column layout makes cards full width; small ones pair up", () => {
  const u = G.units(440, 1000, "column", "comfortable");
  assert.equal(G.sizeFor({ size: "l" }, u).w, u.cols);
  assert.equal(G.sizeFor({ size: "s" }, u).w, u.cols / 2);
});

test("strip: big cards take the full height, small ones stack two high", () => {
  const u = G.units(3000, 500, "strip", "comfortable");
  assert.equal(G.sizeFor({ size: "xl" }, u).h, u.rows);
  assert.equal(G.sizeFor({ size: "s" }, u).h, Math.floor(u.rows / 2));
  const layout = G.pack([{ id: "a", w: 3, h: 5 }, { id: "b", w: 3, h: 5 }], u);
  assert.deepEqual(byId(layout).b, { id: "b", w: 3, h: 5, x: 0, y: 5 });
});

test("pack fills holes and never overlaps", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const items = [{ id: "a", w: 6, h: 4 }, { id: "b", w: 8, h: 3 }, { id: "c", w: 6, h: 2 }, { id: "d", w: 4, h: 6 }];
  const layout = G.pack(items, u);
  noOverlaps(layout);
  inBounds(layout, u);
  assert.deepEqual([byId(layout).c.x, byId(layout).c.y], [6, 0]); // c fits beside a
});

test("moving a card onto another pushes it out of the way, then everything falls up", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const layout = [{ id: "a", x: 0, y: 0, w: 6, h: 4 }, { id: "b", x: 0, y: 4, w: 6, h: 4 }];
  const next = G.place(layout, { id: "b", x: 0, y: 0, w: 6, h: 4 }, u);
  noOverlaps(next);
  assert.equal(byId(next).b.y, 0);
  assert.equal(byId(next).a.y, 4); // swapped
});

test("dragging a card into empty space below falls back up (gravity)", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const next = G.place([{ id: "a", x: 0, y: 0, w: 6, h: 4 }], { id: "a", x: 3, y: 20, w: 6, h: 4 }, u);
  assert.deepEqual(byId(next).a, { id: "a", x: 3, y: 0, w: 6, h: 4 });
});

test("resizing wider pushes the neighbour down, not off the grid", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const layout = [{ id: "a", x: 0, y: 0, w: 6, h: 4 }, { id: "b", x: 6, y: 0, w: 6, h: 4 }];
  const next = G.place(layout, { id: "a", x: 0, y: 0, w: 9, h: 4 }, u);
  noOverlaps(next);
  inBounds(next, u);
  assert.equal(byId(next).b.y, 4);
});

test("a card can't be dropped past the edge", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const next = G.place([{ id: "a", x: 0, y: 0, w: 4, h: 4 }], { id: "a", x: 30, y: -5, w: 40, h: 4 }, u);
  inBounds(next, u);
  assert.equal(byId(next).a.w, u.cols);
});

test("strip gravity is leftward", () => {
  const u = G.units(3000, 500, "strip", "comfortable");
  const next = G.place([{ id: "a", x: 0, y: 0, w: 3, h: 10 }, { id: "b", x: 9, y: 0, w: 3, h: 10 }], { id: "b", x: 9, y: 0, w: 3, h: 10 }, u);
  assert.equal(byId(next).b.x, 3);
});

test("a saved layout adapts to a narrower window instead of breaking", () => {
  const wide = G.units(1600, 900, "window", "comfortable");
  const narrow = G.units(800, 900, "window", "comfortable");
  const saved = { a: { x: 0, y: 0, w: 8, h: 4 }, b: { x: 8, y: 0, w: 8, h: 4 } };
  const out = G.adapt(saved, wide, narrow);
  noOverlaps(out);
  inBounds(out, narrow);
});

test("arrange: smart default puts what matters first and alerts move cards up", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const metas = { repos: { size: "m" }, system: { size: "l" }, links: { size: "m" } };
  let r = G.arrange(["repos", "links", "system"], { u, metas, saved: {}, entries: {}, alerts: [] });
  assert.equal(r.auto, true);
  const first = r.layout.slice().sort((a, b) => a.y - b.y || a.x - b.x)[0];
  assert.equal(first.id, "system");
  r = G.arrange(["repos", "links", "system"], { u, metas, saved: {}, entries: {}, alerts: [{ widget: "links", level: "bad" }] });
  const top = r.layout.slice().sort((a, b) => a.y - b.y || a.x - b.x)[0];
  assert.equal(top.id, "links");
});

test("arrange: saved positions are kept and a new card finds a free spot", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const key = G.profileKey(u);
  const saved = { [key]: { system: { x: 6, y: 0, w: 6, h: 4 } } };
  const r = G.arrange(["system", "memory"], { u, metas: { memory: { size: "m" } }, saved, entries: {}, alerts: [] });
  assert.equal(r.auto, false);
  assert.equal(byId(r.layout).system.x, 6);
  noOverlaps(r.layout);
});

test("arrange: a strip uses the nearest saved strip when its height changes", () => {
  const u = G.units(3000, 500, "strip", "comfortable");
  const saved = { "strip:8": { system: { x: 4, y: 0, w: 4, h: 8 } } };
  const r = G.arrange(["system"], { u, metas: {}, saved, entries: {}, alerts: [] });
  assert.equal(byId(r.layout).system.h, 10);
});

test("content height decides a default card's height, within limits", () => {
  const u = G.units(1200, 800, "window", "comfortable");
  const short = G.sizeFor({ size: "m" }, u, 100);
  const tall = G.sizeFor({ size: "m" }, u, 3000);
  assert.ok(short.h < tall.h);
  assert.ok(tall.h <= Math.round(G.sizeFor({ size: "m" }, u).h * 1.5));
});
