/*
 * 計画の計算（公開ページ用）。backend/app/plan_calc.py と同じ手順を、ブラウザで行う。
 *
 * - PC のサーバーに頼らない。公開ページ（GitHub Pages）に埋め込み、携帯でもどこでも動く。
 * - 10進のまま計算する（BigInt の固定小数点、小数18桁）。2進の浮動小数点に直さない。
 * - 入力は plan_snapshot.py が作ったスナップショット（候補・規則・刻限）と、
 *   利用者が選んだ向き、MT4 の売値（Bid）・買値（Ask）。
 * - Python 版と結果が一致することを tests/test_plancalc_js.py で確かめている。
 *   どちらかを直したら、もう一方も直すこと。
 * - 発注はしない。儲かる根拠ではない。
 */
(function (root) {
  "use strict";

  var P = 18n;
  var S = 10n ** P;

  // ---------------------------------------------------------------- 10進数

  function dec(str) {
    if (str === null || str === undefined) return null;
    var s = String(str).trim();
    var m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(s);
    if (!m) return NaN;
    var frac = (m[3] || "").slice(0, Number(P));
    while (frac.length < Number(P)) frac += "0";
    var v = BigInt(m[2]) * S + BigInt(frac || "0");
    return m[1] ? -v : v;
  }
  function isDec(x) { return typeof x === "bigint"; }
  function fromInt(n) { return BigInt(n) * S; }
  function mul(a, b) { return (a * b) / S; }
  function div(a, b) { return (a * S) / b; }
  function abs(a) { return a < 0n ? -a : a; }
  function floorDiv(a, b) {          // a, b は整数（BigInt）。数学的な floor
    var q = a / b;
    if ((a % b !== 0n) && ((a < 0n) !== (b < 0n))) q -= 1n;
    return q;
  }
  function quant(x, tick, up) {      // 呼値に量子化（up: 切り上げ / 切り下げ）
    var n = floorDiv(x, tick);
    if (up && n * tick !== x) n += 1n;
    return n * tick;
  }
  function intUp(x) { var n = floorDiv(x, S); return n * S === x ? n : n + 1n; }
  function intDown(x) { return floorDiv(x, S); }
  function places(str) { var i = String(str).indexOf("."); return i < 0 ? 0 : String(str).length - i - 1; }
  function fmt(x, nPlaces) {
    var neg = x < 0n; var v = neg ? -x : x;
    var ip = v / S; var fp = (v % S).toString().padStart(Number(P), "0");
    var out = ip.toString();
    if (nPlaces > 0) out += "." + fp.slice(0, nPlaces);
    return (neg ? "-" : "") + out;
  }
  function yen(n) { return Number(n).toLocaleString("ja-JP"); }

  // ---------------------------------------------------------------- 計算

  function stop(code, message) { return { code: code, message: message }; }

  function draft(snap, direction, bidStr, askStr, now) {
    var ins = snap.instrument, acc = snap.account, r = snap.rules, c = snap.costs;
    var side = direction === "LONG" ? snap.long : snap.short;
    var bid = dec(bidStr), ask = dec(askStr);
    var atr = dec(snap.atr), close = dec(snap.reference_close);
    var balance = dec(acc.balance), riskPct = dec(acc.risk_pct);
    var pip = dec(ins.pip), tick = dec(ins.price_tick);
    var nums = [bid, ask, atr, close, balance, riskPct, pip, tick];
    for (var k = 0; k < nums.length; k++) {
      if (!isDec(nums[k]) || nums[k] <= 0n)
        return { plan: null, stops: [stop("PLAN_INVALID_NUMBER", "価格・ATR・資金・リスク割合に、正の有限の数でないものがあります")] };
    }
    var out = [];
    var gen = new Date(snap.generated_at);
    if (direction !== "LONG" && direction !== "SHORT") out.push(stop("DIRECTION_UNSET", "向きが選ばれていません"));
    if (now.getTime() >= gen.getTime() + snap.valid_minutes * 60000)
      out.push(stop("PLAN_EXPIRED", "候補（構造・ATR）の有効期限が切れています"));
    if (new Date(snap.last_bar_at).getTime() > gen.getTime())
      out.push(stop("FUTURE_BAR_USED", "判定した時刻より後の足を使っています"));
    var wide = dec(c.wide_spread), xWide = dec(c.max_spread_x_wide), gapAtr = dec(c.max_gap_atr);
    if (bid >= ask) {
      out.push(stop("QUOTE_INVALID", "売値（Bid）が買値（Ask）以上になっています。MT4 の気配値表示の2つの値をそのまま入れてください（買値の方が少し高い。向きに関係なく同じ）"));
    } else if (isDec(wide) && (ask - bid) > mul(wide, xWide)) {
      out.push(stop("QUOTE_INVALID", "売値と買値の差が、ふだんの広いときの " + c.max_spread_x_wide + " 倍を超えています。建値や利確ではなく、気配値表示の売値・買値を入れてください（指標時の拡大なら、落ち着いてから）"));
    }
    var mid = (bid + ask) / 2n;
    if (abs(mid - close) >= mul(atr, gapAtr))
      out.push(stop("QUOTE_MISMATCH", "手で入れた値が、取り込んだ足の終値から大きく離れています（別の銘柄の値を入れた疑い）"));
    if (out.length) return { plan: null, stops: out };

    var long = direction === "LONG";
    var pricePlaces = places(ins.price_tick);

    // C 実効建値（不利側）
    var entry = long ? quant(ask, tick, true) : quant(bid, tick, false);

    // D 損切り
    var atrMul = dec(r.atr_multiple), minAtr = dec(r.min_atr_multiple), buf = dec(r.structure_buffer_atr);
    var atrStop = long ? entry - mul(atrMul, atr) : entry + mul(atrMul, atr);
    var sl = atrStop, basis = "ATR の " + r.atr_multiple + " 倍";
    var swing = dec(side.swing);
    if (r.use_structure && isDec(swing)) {
      var s = long ? swing - mul(buf, atr) : swing + mul(buf, atr);
      var farther = long ? s < atrStop : s > atrStop;
      if (farther && abs(entry - s) >= mul(minAtr, atr)) {
        sl = s; basis = long ? "直近の転換安値の外側" : "直近の転換高値の外側";
      }
    } else if (!isDec(swing)) {
      basis += "（転換点なし）";
    }
    sl = quant(sl, tick, !long);
    if ((long && sl >= entry) || (!long && sl <= entry) || sl <= 0n)
      return { plan: null, stops: [stop("STOP_INVALID", "損切りが建値の反対側に来ています")] };
    if (abs(entry - sl) < mul(minAtr, atr))
      return { plan: null, stops: [stop("STOP_INVALID", "損切りが近すぎます（ATR の最小倍数に届きません）")] };

    // F 費用込みの距離
    var slip = mul(dec(c.slippage_pips_per_side), pip);
    var gross = abs(entry - sl);
    var lossDist = gross + 2n * slip;

    // E 利確（R は費用込み）
    var barrier = dec(side.barrier), offset = dec(r.barrier_offset_atr);
    var raw = [];
    for (var i = 0; i < r.r_multiples.length; i++) {
      var m = dec(r.r_multiples[i]);
      var dist = mul(m, lossDist) + 2n * slip;
      var price = long ? entry + dist : entry - dist;
      var capped = null;
      if (isDec(barrier) && (long ? price > barrier : price < barrier)) {
        price = long ? barrier - mul(offset, atr) : barrier + mul(offset, atr);
        capped = long ? "抵抗帯" : "支持帯";
        price = quant(price, tick, !long);
      } else {
        price = quant(price, tick, long);
      }
      if (raw.length && raw[raw.length - 1].price === price) continue;
      raw.push({ price: price, capped: capped });
    }

    // G 円換算（決済通貨が円の銘柄だけ。それ以外は止める）
    var stress = dec(c.conversion_stress);
    var one = fromInt(1);
    if (ins.quote !== acc.currency)
      return { plan: null, stops: [stop("CONVERSION_MISSING", ins.quote + " を円に直すレートがありません。1.0 では代用しません")] };
    var lossRate = one + stress, gainRate = one - stress;

    // H 数量
    var maxRisk = dec(acc.max_risk_pct);
    if (riskPct > maxRisk)
      return { plan: null, stops: [stop("RISK_ABOVE_CAP", "1回のリスク " + acc.risk_pct + "% が上限 " + acc.max_risk_pct + "% を超えています")] };
    var riskAmount = mul(balance, riskPct) / 100n;
    var lossPerQty = mul(lossDist * BigInt(ins.contract_unit), lossRate);
    var qty = Number(floorDiv(div(riskAmount, lossPerQty), S));
    qty -= qty % ins.qty_step;
    var notes = [];
    if (qty > ins.max_qty) {
      qty = ins.max_qty - ins.max_qty % ins.qty_step;
      notes.push("数量は上限 " + ins.max_qty + " までです");
    }
    while (qty >= ins.min_qty && intUp(lossPerQty * BigInt(qty)) * S > riskAmount) qty -= ins.qty_step;
    if (qty < ins.min_qty)
      return { plan: null, stops: [stop("QTY_BELOW_MIN", "許容損失 " + yen(intDown(riskAmount)) + "円 では数量" + ins.min_qty + "（" + yen(ins.contract_unit * ins.min_qty) + "通貨）も建てられません（数量1で約 " + yen(intUp(lossPerQty)) + "円 の損失）")] };
    var units = BigInt(qty * ins.contract_unit);
    var lossJpy = intUp(mul(lossDist * units, lossRate));

    var targets = [];
    for (var j = 0; j < raw.length; j++) {
      var gain = (long ? raw[j].price - entry : entry - raw[j].price) - 2n * slip;
      if (gain <= 0n) continue;
      var net = floorDiv(div(gain, lossDist), S / 100n) * (S / 100n);
      targets.push({
        label: "TP" + (targets.length + 1), price: fmt(raw[j].price, pricePlaces),
        net_rr: fmt(net, 2), profit_jpy: Number(intDown(mul(gain * units, gainRate))),
        capped_by: raw[j].capped, _price: raw[j].price,
      });
    }
    if (!targets.length)
      return { plan: null, stops: [stop("TARGET_NO_ROOM", "利確を置ける余地がありません（帯が近すぎます）")] };
    var firstGain = (long ? targets[0]._price - entry : entry - targets[0]._price) - 2n * slip;
    if (div(firstGain, lossDist) < dec(r.min_rr))
      return { plan: null, stops: [stop("NET_RR_BELOW_MIN", "費用込みの TP1 までの比が " + targets[0].net_rr + " で、下限 " + r.min_rr + " に届きません")] };

    // I 証拠金
    var mRate;
    if (ins.base === acc.currency) mRate = one;
    else if (ins.base + acc.currency === ins.symbol) mRate = ask;
    else return { plan: null, stops: [stop("CONVERSION_MISSING", ins.base + " を円に直すレートがありません。1.0 では代用しません")] };
    var margin = intUp(mul(mul(units * mRate, one + stress), dec(acc.margin_rate)));
    var marginPct = div(margin * S * 100n, balance);
    if (marginPct > dec(acc.max_margin_pct))
      return { plan: null, stops: [stop("MARGIN_ABOVE_CAP", "必要証拠金（推定）が資金の " + fmt(marginPct, 1) + "% で、上限 " + acc.max_margin_pct + "% を超えます")] };

    targets.forEach(function (t) { delete t._price; });
    return {
      plan: {
        direction: direction, entry: fmt(entry, pricePlaces), stop: fmt(sl, pricePlaces),
        stop_basis: basis, qty: qty, units: Number(units), loss_jpy: Number(lossJpy),
        risk_amount_jpy: Number(intDown(riskAmount)), margin_jpy: Number(margin),
        margin_pct: fmt(marginPct + (S / 20n), 1), targets: targets, notes: notes,
      },
      stops: [],
    };
  }

  function gate(snap, now) {
    var out = [];
    if (snap.board.excluded) {
      var why = snap.board.reasons.join("・") || "理由の記載なし";
      out.push(stop("BOARD_EXCLUDED", "ボードが除外の日です（" + why + "）。向きを選んでも計画は出しません"));
    }
    if (snap.deadline) {
      var dl = new Date(snap.deadline);
      var left = (dl.getTime() - now.getTime()) / 60000;
      var hhmm = dl.toLocaleTimeString("ja-JP", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit" });
      if (left <= 0) out.push(stop("DEADLINE", "今日の手仕舞いの刻限（" + hhmm + "）を過ぎています。翌日に持ち越さないため、次の取引日（日替わり後）まで建てません"));
      else if (left < snap.min_minutes_to_exit) out.push(stop("DEADLINE", "手仕舞いの刻限まで " + Math.round(left) + " 分しかありません（" + snap.min_minutes_to_exit + " 分以上が必要）。翌日に持ち越さないため、ここでは建てません"));
      if (dl.getTime() > new Date(snap.next_rollover).getTime())
        out.push(stop("ROLLOVER_CROSS", "手仕舞いの刻限が日替わりを跨ぎます（スワップが付く）"));
    }
    var ins = snap.instrument;
    if (!ins.verified_on || !ins.course || ins.course !== snap.account.course)
      out.push(stop("SPEC_UNVERIFIED", "取引単位・数量の条件が、使っている口座のコースで確かめられていません"));
    return out;
  }

  function decide(snap, direction, bid, ask, now) {
    now = now || new Date();
    var d = draft(snap, direction, bid, ask, now);
    var stops = d.stops.slice();
    gate(snap, now).forEach(function (g) {
      if (!stops.some(function (s) { return s.code === g.code && s.message === g.message; })) stops.push(g);
    });
    if (stops.length || !d.plan) return { status: "NO_TRADE", stops: stops, plan: null };
    return { status: "PLAN_OK", stops: [], plan: d.plan };
  }

  var api = { decide: decide, draft: draft, gate: gate, _dec: dec, _fmt: fmt };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PlanCalc = api;
})(typeof self !== "undefined" ? self : this);
