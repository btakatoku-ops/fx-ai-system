import assert from "node:assert/strict";
import { test } from "node:test";

import { isExpired, secondsRemaining, yen } from "./freshness.ts";

const T = Date.parse("2026-09-14T12:00:00Z");

test("期限前は有効、期限ちょうどからは切れている", () => {
  const until = "2026-09-14T12:00:00Z";
  assert.equal(isExpired(until, T - 1000), false);
  assert.equal(isExpired(until, T), true);
  assert.equal(isExpired(until, T + 1000), true);
});

test("valid_until が無ければ切れていない扱い", () => {
  assert.equal(isExpired(null, T), false);
  assert.equal(secondsRemaining(null, T), null);
});

test("読めない日時は『切れている』とみなす", () => {
  // 読めないものを有効として通すと、根拠不明の BUY が画面に残る
  assert.equal(isExpired("not-a-date", T), true);
});

test("残り秒を返す（切れていれば0以下）", () => {
  assert.equal(secondsRemaining("2026-09-14T12:00:30Z", T), 30);
  assert.equal(secondsRemaining("2026-09-14T11:59:50Z", T), -10);
});

test("金額はプラスにも符号を付ける", () => {
  assert.equal(yen({ amount: 1234.6, currency: "JPY" }), "+1,235 円");
  assert.equal(yen({ amount: -220, currency: "JPY" }), "−220 円");
  assert.equal(yen(null), "—");
});
