# -*- coding: utf-8 -*-
"""ボードの再生（過去の足で作り直して数える）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app import board as B
from app import board_replay as R

UTC = timezone.utc


def test_board_times_are_the_record_times(cfg):
    ts = R.board_times(cfg, date(2026, 10, 5), date(2026, 10, 11))   # 月〜日
    assert len(ts) == 15                                              # 平日5日×3
    days = {t.astimezone(R.JST).date() for t, _ in ts}
    assert date(2026, 10, 10) not in days and date(2026, 10, 11) not in days
    first = [t for t, s in ts if t.astimezone(R.JST).date() == date(2026, 10, 5)]
    assert [t.astimezone(R.JST).strftime("%H:%M") for t in first] == ["08:42", "16:00", "21:30"]


def test_fundamentals_and_brief_are_off_only_inside(cfg):
    real_f, real_b = B.fundamentals_factor, B._brief_for
    with R._no_fundamentals():
        f = B.fundamentals_factor(cfg, "USDJPY", datetime.now(UTC))
        assert f.view == B.NONE and B._brief_for("USDJPY", datetime.now(UTC)) is None
    assert B.fundamentals_factor is real_f and B._brief_for is real_b      # 元に戻す


def test_the_replay_does_not_see_the_future(cfg, mock_provider):
    """**未来を見ない。** その時刻より後ろの足を持っていてもいなくても、判定は同じ。"""
    sym = "USDJPY"
    hist = R.load_history(mock_provider, sym)
    spread = mock_provider.get_spread(sym)
    m15 = hist["M15"].candles
    t = m15[-60].timestamp + timedelta(minutes=2)
    cut = {tf: type(s)(pair=s.pair, timeframe=s.timeframe,
                       candles=[c for c in s.candles if c.timestamp < t])
           for tf, s in hist.items()}
    with R._no_fundamentals():
        a = R.replay_pair(cfg, sym, hist, spread, [(t, "london")])
        cut["M15"].candles.append(m15[-59])                  # 結果を測るのに次の足だけ足す
        b = R.replay_pair(cfg, sym, cut, spread, [(t, "london")])
    assert a and b
    for k in ("state", "lean", "exclude", "factors", "price", "atr"):
        assert a[0].get(k) == b[0].get(k), k


def test_a_win_is_the_tp1_multiple_and_a_loss_is_minus_one(cfg, mock_provider):
    sym = "USDJPY"
    hist = R.load_history(mock_provider, sym)
    rows = R.replay_pair(cfg, sym, hist, mock_provider.get_spread(sym),
                         [(c.timestamp + timedelta(minutes=2), "tokyo")
                          for c in hist["M15"].candles[-400:-40:20]])
    m = float(cfg.account["targets"]["r_multiples"][0])
    for r in rows:
        for side in ("long", "short"):
            if r.get(f"{side}_outcome") == "WIN":
                assert abs(r[f"{side}_r"] - m) < 1e-6
            if r.get(f"{side}_outcome") == "LOSS":
                assert abs(r[f"{side}_r"] + 1) < 1e-6


def test_wilson_interval():
    lo, hi = R.wilson(14, 49)
    assert 0.17 < lo < 0.19 and 0.41 < hi < 0.43
    assert R.wilson(0, 0) == (None, None)


def test_summary_compares_with_both_directions():
    rows = [{"pair": "X", "at": "t", "session": "tokyo", "lean": "up", "excluded": False,
             "exclude": [], "seg_move": 0.2, "move": -0.1,
             "long_outcome": "WIN", "long_r": 1.5, "short_outcome": "LOSS", "short_r": -1.0},
            {"pair": "X", "at": "t", "session": "london", "lean": None, "excluded": True,
             "exclude": ["H1 と H4 の向きが逆です"], "seg_move": 0.1, "move": 0.1,
             "long_outcome": "LOSS", "long_r": -1.0, "short_outcome": "TIMEOUT", "short_r": 0.2}]
    s = R.summarize(rows)
    g = s["groups"]["ボードが「そろった」（除外なし）"]
    assert g["tp1"]["n"] == 1 and g["tp1"]["win"] == 1               # 向きの側（買い）で数える
    assert s["baseline"]["tp1"]["n"] == 4                             # 全時刻×買い・売り
    assert s["excluded_share"] == 0.5


def test_resim_changes_only_the_target(cfg, mock_provider):
    """利確の倍率だけを変える。勝てばその倍率、負ければ −1（同じ損切り）。"""
    sym = "USDJPY"
    hist = R.load_history(mock_provider, sym)
    times = [(c.timestamp + timedelta(minutes=2), "tokyo") for c in hist["M15"].candles[-400:-40:40]]
    rows = R.replay_pair(cfg, sym, hist, mock_provider.get_spread(sym), times)
    sim = R.resim(cfg, rows, {sym: list(hist["M15"].candles)}, (1.0, 3.0))
    assert sim
    for res in sim.values():
        for m in (1.0, 3.0):
            for o, r in res[m].values():
                if o == "WIN":
                    assert abs(r - m) < 1e-6
                if o == "LOSS":
                    assert abs(r + 1) < 1e-6
    # 計画の規則の倍率で測り直せば、再生の結果と一致する
    m0 = float(cfg.account["targets"]["r_multiples"][0])
    again = R.resim(cfg, rows, {sym: list(hist["M15"].candles)}, (m0,))
    for r in rows:
        k = f"{r['pair']}|{r['at']}"
        if k in again:
            assert again[k][m0]["long"] == (r["long_outcome"], r["long_r"])


def test_archive_accumulates_and_restarts_when_rules_change(tmp_path):
    p = tmp_path / "archive.json"
    a = {"pair": "X", "at": "2026-10-01T00:00:00+00:00", "v": 1}
    b = {"pair": "X", "at": "2026-10-02T00:00:00+00:00", "v": 1}
    assert len(R.merge_archive(p, [a], "fp1")) == 1
    merged = R.merge_archive(p, [dict(a, v=2), b], "fp1")         # 同じ時刻は置き換え
    assert len(merged) == 2 and merged[0]["v"] == 2
    fresh = R.merge_archive(p, [b], "fp2")                         # 規則が変わったら混ぜない
    assert len(fresh) == 1 and (tmp_path / "archive-fp1.json").exists()


def test_fingerprint_follows_the_rules(cfg):
    from types import SimpleNamespace
    fp = R.rules_fingerprint(cfg)
    other = SimpleNamespace(filters=cfg.filters, board=cfg.board, spreads=cfg.spreads,
                            account={**cfg.account, "targets": {"r_multiples": [2.0], "min_rr": 2.0}})
    assert R.rules_fingerprint(other) != fp
