import { strict as assert } from "node:assert";
import { test } from "node:test";

import { entryMid, planMarks, type PlanMarks } from "./chart.ts";

const fmt = (v: number) => v.toFixed(3);

const base: PlanMarks = {
  direction: "LONG",
  signalLabel: "▲ 買い",
  entryLow: 150.0,
  entryHigh: 150.2,
  stop: 149.5,
  targets: [
    { label: "TP1", price: 150.95, r: 1.5 },
    { label: "TP2", price: 151.45, r: 2.5 },
  ],
  ok: true,
  note: null,
};

test("建玉の線は Entry・SL・TP をこの順で出す", () => {
  const marks = planMarks(base, fmt);
  assert.deepEqual(
    marks.map((m) => m.kind),
    ["entry", "stop", "target", "target"]
  );
  assert.equal(marks[0].text, "Entry 150.000〜150.200");
  assert.equal(marks[1].text, "SL 149.500");
  assert.equal(marks[2].text, "TP1 150.950（1.5R）");
});

test("入る値は上下の中央に引く", () => {
  assert.equal(entryMid(base), 150.1);
  assert.equal(entryMid({ ...base, entryHigh: null }), null);
  assert.equal(entryMid(null), null);
});

test("計画が成り立っていなければ線を出さない", () => {
  // **損切りを置けない場面で線だけ引くと、建てられるように見える。**
  assert.deepEqual(planMarks({ ...base, ok: false }, fmt), []);
});

test("期限切れのときは線を出さない", () => {
  // 呼び出し側は期限切れを ok=false にして渡す。根拠が失われた値を、
  // まだ有効な目安のように見せないため。
  const expired: PlanMarks = { ...base, ok: false, note: "この判断は期限切れです" };
  assert.deepEqual(planMarks(expired, fmt), []);
});

test("値は丸めない。engine の出した値をそのまま引く", () => {
  const odd = { ...base, stop: 149.5432, entryLow: 150.0001, entryHigh: 150.0003 };
  const marks = planMarks(odd, (v) => String(v));
  assert.equal(marks[1].value, 149.5432);
  assert.equal(marks[0].value, (150.0001 + 150.0003) / 2);
});

test("欠けている値は飛ばす。入っているふりをしない", () => {
  const noStop = { ...base, stop: null, targets: [] };
  const marks = planMarks(noStop, fmt);
  assert.deepEqual(
    marks.map((m) => m.kind),
    ["entry"]
  );
});

test("計画そのものが無ければ空", () => {
  assert.deepEqual(planMarks(null, fmt), []);
  assert.deepEqual(planMarks(undefined, fmt), []);
});
