import assert from "node:assert/strict";
import { test } from "node:test";

import type { BoardFacts } from "./api.ts";
import { fmt, nearLevels, rangeBar, usedLevel } from "./board.ts";

const facts = (over: Partial<BoardFacts> = {}): BoardFacts => ({
  price: 208.25,
  price_at: null,
  today_open: 209.95,
  today_high: 210.08,
  today_low: 208.0,
  today_change_pct: -0.8,
  prev_high: 210.14,
  prev_low: 208.76,
  prev_note: null,
  adr: 1.69,
  adr_days: 20,
  used_ratio: 1.23,
  range_high: 217.05,
  range_low: 207.08,
  position: 0.12,
  room_up_adr: 5.2,
  room_down_adr: 0.69,
  round_above: 208.5,
  round_below: 208.0,
  session: "",
  minutes_to_deadline: null,
  deadline_jst: null,
  next_event: null,
  minutes_to_event: null,
  event_note: null,
  market_open: true,
  day_note: null,
  levels: [
    { price: 207.08, label: "20日安値", kind: "support" },
    { price: 208.0, label: "キリ番", kind: "round" },
    { price: 208.5, label: "キリ番", kind: "round" },
    { price: 208.76, label: "前日安値", kind: "support" },
    { price: 210.14, label: "前日高値", kind: "resistance" },
    { price: 217.05, label: "20日高値", kind: "resistance" },
  ],
  ...over,
});

test("帯の上の位置は 0〜100 に収まる", () => {
  const b = rangeBar(facts())!;
  assert.ok(b.price! > 0 && b.price! < 20);
  assert.ok(b.todayFrom! < b.todayTo!);
});

test("今日が20日の外に出ていれば、帯を広げて描く（はみ出さない）", () => {
  const b = rangeBar(facts({ today_low: 206.0, price: 206.1 }))!;
  assert.equal(b.lo, 206.0);
  assert.equal(b.todayFrom, 0);
  assert.ok(b.price! > 0);
});

test("20日の高安が無ければ帯を描かない", () => {
  assert.equal(rangeBar(facts({ range_low: null })), null);
  assert.equal(rangeBar(facts({ range_low: 210, range_high: 210, today_low: null, today_high: null })), null);
});

test("近い水準を上と下から近い順に", () => {
  const { above, below } = nearLevels(facts());
  assert.deepEqual(above.map((l) => l.price), [208.5, 208.76]);
  assert.deepEqual(below.map((l) => l.price), [208.0, 207.08]);
  assert.ok(above[0].adr! > 0 && below[0].adr! < 0);
});

test("値が無ければ水準も出さない", () => {
  assert.deepEqual(nearLevels(facts({ price: null })), { above: [], below: [] });
});

test("消化率の区切りは 0.9 と 1.2", () => {
  assert.equal(usedLevel(null), "none");
  assert.equal(usedLevel(0.5), "ok");
  assert.equal(usedLevel(0.9), "caution");
  assert.equal(usedLevel(1.2), "exclude");
});

test("値が無いところは — にする", () => {
  assert.equal(fmt(null, 3), "—");
  assert.equal(fmt(208.2469, 3), "208.247");
});
