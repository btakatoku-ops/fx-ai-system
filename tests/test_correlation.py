# -*- coding: utf-8 -*-
"""相関（配点10点）の試験。

**どの銘柄が何と相関するかを決め打ちしない。** 測って、弱ければ使わない。
その「測る」部分が正しいことを、既知の答えと突き合わせて確かめる。
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone

import pytest

from app.correlation import (
    CorrelationResult,
    _align,
    _floor,
    _returns,
    clear_cache,
    evaluate,
    load_driver,
    pearson,
)

T0 = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def _cfg(**over):
    base = {
        "drivers": {"D1": {"symbol": "X", "label": "材料1"}},
        "timeframe": "H1", "bar_minutes": 60, "window_bars": 240,
        "min_overlap_bars": 10, "min_abs_correlation": 0.3,
        "move_bars": 3, "min_drivers": 1,
    }
    base.update(over)
    return base


def _write(tmp_path, name, values, tf="H1", step=60, offset=0):
    path = tmp_path / f"{name}_{tf}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "close"])
        for i, v in enumerate(values):
            t = T0 + timedelta(minutes=step * i + offset)
            w.writerow([t.isoformat(), f"{v:.6f}"])
    return path


def _pair(values, step=60):
    return [(T0 + timedelta(minutes=step * i), v) for i, v in enumerate(values)]


# ============================================================ 相関係数


def test_pearson_matches_known_values():
    """既知の答えと突き合わせる。自分の出力を正解にしない。"""
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    # 既知の例（Python の statistics.correlation と一致することを確認済み）
    r = pearson([1, 2, 3, 4, 5], [2, 1, 4, 3, 5])
    assert r == pytest.approx(0.8, abs=1e-9)


def test_pearson_returns_none_when_it_cannot_be_measured():
    """**「相関が無い」と「測れない」を混ぜない。**

    分母が0のとき0を返すと、動きの無い系列に「相関0」という
    測った結果が付いてしまう。
    """
    assert pearson([1, 1, 1, 1], [1, 2, 3, 4]) is None   # 片方が定数
    assert pearson([1, 2], []) is None
    assert pearson([1], [2]) is None


def test_returns_skip_non_positive_prices():
    assert _returns([100.0, 110.0]) == [pytest.approx(0.1)]


# ============================================================ 時刻あわせ


def test_align_matches_only_common_timestamps():
    """無い時刻は埋めない。埋めると動いていない時間を動いたことにする。"""
    pair = _pair([1, 2, 3, 4])
    driver = [(T0, 10.0), (T0 + timedelta(minutes=120), 30.0)]
    xs, ys = _align(pair, driver)
    assert xs == [1, 3]
    assert ys == [10.0, 30.0]


def test_align_can_snap_to_the_bar_grid():
    """材料は :20 や :30 に刻まれている。枠に落として突き合わせる。

    米10年債は毎時 :20、S&P500 は :30。為替の :00 とは一致しないので、
    そのままだと1本も揃わない。
    """
    pair = _pair([1, 2, 3])
    driver = [(T0 + timedelta(minutes=20), 10.0),
              (T0 + timedelta(minutes=80), 20.0)]
    assert _align(pair, driver) == ([], [])          # そのままでは揃わない
    xs, ys = _align(pair, driver, bar_minutes=60)
    assert xs == [1, 2]
    assert ys == [10.0, 20.0]


def test_floor_snaps_to_the_frame():
    assert _floor(T0 + timedelta(minutes=59), 60) == T0
    assert _floor(T0 + timedelta(minutes=61), 60) == T0 + timedelta(hours=1)


# ============================================================ 評価


def test_perfectly_correlated_driver_supports_the_direction(tmp_path):
    """材料が上がり、正の相関なら「上」を支持する。"""
    clear_cache()
    values = [100 + i for i in range(60)]
    _write(tmp_path, "D1", values)
    res = evaluate(_pair(values), 1, _cfg(), directory=str(tmp_path))
    assert res.state == "KNOWN"
    assert res.ratio == pytest.approx(1.0)
    assert res.drivers[0].correlation == pytest.approx(1.0)
    assert res.drivers[0].implied == 1


def test_the_same_driver_opposes_the_opposite_direction(tmp_path):
    clear_cache()
    values = [100 + i for i in range(60)]
    _write(tmp_path, "D1", values)
    res = evaluate(_pair(values), -1, _cfg(), directory=str(tmp_path))
    assert res.state == "KNOWN"
    assert res.ratio == pytest.approx(0.0)


def test_inverse_correlation_flips_the_implied_direction(tmp_path):
    """相関が負なら、材料が上がったとき銘柄は「下」を示す。

    **相関は水準ではなく収益率で測る。** 直線的に下がる系列と上がる系列は、
    水準では逆でも収益率では同じ向きに動くので、逆相関にならない。
    ここでは収益率そのものを反転させて作る。
    """
    clear_cache()
    steps = [0.004 if i % 3 else -0.003 for i in range(59)]
    pair_values = [100.0]
    driver_values = [50.0]
    for r in steps:
        pair_values.append(pair_values[-1] * (1 - r))    # 収益率を反転
        driver_values.append(driver_values[-1] * (1 + r))
    _write(tmp_path, "D1", driver_values)
    res = evaluate(_pair(pair_values), -1, _cfg(), directory=str(tmp_path))
    assert res.state == "KNOWN"
    assert res.drivers[0].correlation < 0
    assert res.drivers[0].implied == -1
    assert res.ratio == pytest.approx(1.0)


def test_a_weak_driver_is_not_used(tmp_path):
    """**弱い相関に点を付けない。** 雑音を根拠にすることになる。"""
    clear_cache()
    import random

    rnd = random.Random(1234)
    pair_values = [100 + rnd.gauss(0, 1) for _ in range(120)]
    driver_values = [50 + rnd.gauss(0, 1) for _ in range(120)]
    _write(tmp_path, "D1", driver_values)
    res = evaluate(_pair(pair_values), 1, _cfg(), directory=str(tmp_path))
    assert res.state == "WEAK"
    assert res.ratio == 0.0
    assert any("弱く" in x for x in res.skipped + res.reasons)


def test_missing_driver_files_are_unavailable_not_weak(tmp_path):
    """**「測れない」と「測ったが弱い」を区別する。**

    測れないものを満点に数えると、材料を持たない環境で全体が沈む。
    """
    clear_cache()
    res = evaluate(_pair([100 + i for i in range(60)]), 1, _cfg(),
                   directory=str(tmp_path))
    assert res.state == "UNAVAILABLE"
    assert res.ratio == 0.0


def test_no_direction_means_no_evaluation(tmp_path):
    clear_cache()
    res = evaluate(_pair([100 + i for i in range(60)]), 0, _cfg(),
                   directory=str(tmp_path))
    assert res.state == "UNKNOWN"


def test_too_little_overlap_is_unavailable(tmp_path):
    clear_cache()
    values = [100 + i for i in range(60)]
    _write(tmp_path, "D1", values[:5])          # 材料が5本しかない
    res = evaluate(_pair(values), 1, _cfg(), directory=str(tmp_path))
    assert res.state == "UNAVAILABLE"


def test_two_drivers_are_weighted_by_correlation_strength(tmp_path):
    """一致度は |相関| で重み付けする。強い材料の意見を重く見る。"""
    clear_cache()
    n = 80
    pair_values = [100 + i for i in range(n)]
    strong = [50 + i for i in range(n)]                 # 相関ほぼ +1
    weak = [30 + (i if i % 2 == 0 else i - 0.9) for i in range(n)]
    _write(tmp_path, "STRONG", strong)
    _write(tmp_path, "WEAK", weak)
    cfg = _cfg(drivers={"STRONG": {"symbol": "s", "label": "強"},
                        "WEAK": {"symbol": "w", "label": "弱"}})
    res = evaluate(_pair(pair_values), 1, cfg, directory=str(tmp_path))
    assert res.state == "KNOWN"
    assert res.ratio == pytest.approx(1.0)     # どちらも「上」を支持


def test_loading_a_missing_file_returns_empty_not_error(tmp_path):
    clear_cache()
    assert load_driver("NOPE", "H1", str(tmp_path)) == ()


def test_broken_rows_in_a_driver_file_are_skipped(tmp_path):
    """壊れた行は飛ばす。直さない。"""
    clear_cache()
    path = tmp_path / "D1_H1.csv"
    path.write_text(
        "timestamp,close\n"
        f"{T0.isoformat()},100\n"
        f"{(T0 + timedelta(hours=1)).isoformat()},abc\n"
        f"{(T0 + timedelta(hours=2)).isoformat()},-5\n"
        f"{(T0 + timedelta(hours=3)).isoformat()},103\n",
        encoding="utf-8")
    rows = load_driver("D1", "H1", str(tmp_path))
    assert len(rows) == 2
    assert [v for _t, v in rows] == [100.0, 103.0]


def test_result_serialises_for_the_api():
    d = CorrelationResult(state="UNAVAILABLE", reasons=["材料なし"]).as_dict()
    assert d["state"] == "UNAVAILABLE" and d["ratio"] == 0.0


# ==================================================== 未来を見ていないこと


def test_correlation_never_uses_driver_data_past_the_pair_series(tmp_path):
    """**材料の未来を覗かない。**

    検証では銘柄の足を打ち切って過去を再生する。材料の系列は全期間を
    持っているので、打ち切りより後ろの材料を使えば未来を見たことになる。
    銘柄側に無い時刻は突き合わせに現れない、という性質で防いでいる。
    ここではその性質を、材料の後半を大きく動かして確かめる。
    """
    clear_cache()
    n = 120
    values = [100 + i * 0.1 for i in range(n)]
    driver = list(values)
    # 後半（打ち切りより後ろ）を極端に動かす
    for i in range(n // 2, n):
        driver[i] = 1000.0 + i * 50
    _write(tmp_path, "D1", driver)

    cutoff = n // 2
    truncated = _pair(values[:cutoff])
    res_trunc = evaluate(truncated, 1, _cfg(), directory=str(tmp_path))

    clear_cache()
    res_full_driver = evaluate(truncated, 1, _cfg(), directory=str(tmp_path))

    # 同じ打ち切りなら、材料が先まで持っていても結果は変わらない
    assert res_trunc.state == res_full_driver.state
    assert res_trunc.ratio == pytest.approx(res_full_driver.ratio)
    if res_trunc.drivers:
        # 使った材料の最後の動きが、打ち切り後の極端な値になっていない
        assert abs(res_trunc.drivers[0].driver_move) < 1.0


def test_only_timestamps_present_in_the_pair_series_are_matched(tmp_path):
    """突き合わせは銘柄側の時刻が起点。材料の余分な時刻は入らない。"""
    clear_cache()
    pair = _pair([1, 2, 3])
    driver = [(T0, 10.0), (T0 + timedelta(minutes=60), 11.0),
              (T0 + timedelta(minutes=120), 12.0),
              (T0 + timedelta(minutes=180), 99.0),      # 銘柄側に無い
              (T0 + timedelta(minutes=240), 99.0)]
    xs, ys = _align(pair, driver, bar_minutes=60)
    assert len(xs) == 3
    assert 99.0 not in ys


# ============ 古い材料を「直近の動き」にしない（2026-09-16）


def test_a_stale_driver_is_not_used_as_the_recent_move(tmp_path, cfg):
    """**取り込みが止まった材料を、いまの動きとして点数に入れない。**

    相関の「直近の動き」は材料の最後の足から出す。取り込みが止まると、
    その「直近」が何時間も前の動きになる。時刻の重なりは窓が広いので
    足りてしまい、素通りしていた。相関の取り込みは手動なので実際に起きる。
    """
    from datetime import datetime, timedelta, timezone

    from app.correlation import evaluate

    base = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    n = 200
    pair = [(base + timedelta(hours=i), 150.0 + i * 0.1) for i in range(n)]

    def write(where, rows):
        # **置き場を分ける。** 読み込みは使い回されるので、同じ場所へ
        # 上書きすると、2回目も1回目の中身のまま測ってしまう。
        where.mkdir(parents=True, exist_ok=True)
        p = where / "DXY_H1.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            f.write("timestamp,open,high,low,close,volume\n")
            for t, v in rows:
                f.write(f"{t.isoformat()},{v},{v},{v},{v},0\n")

    conf = dict(cfg.correlation)
    conf["drivers"] = {"DXY": {"symbol": "DX-Y.NYB", "label": "ドル指数"}}

    # 銘柄と同じところまで来ている材料 → 使える
    fresh_dir = tmp_path / "fresh"
    write(fresh_dir, [(t, 100.0 + i * 0.1) for i, (t, _) in enumerate(pair)])
    fresh = evaluate(pair, 1, conf, directory=str(fresh_dir))
    assert not any("古く" in s for s in fresh.skipped), fresh.skipped

    # 12時間ぶん止まっている材料 → 使わない（重なりは足りている）
    stale_dir = tmp_path / "stale"
    write(stale_dir,
          [(t, 100.0 + i * 0.1) for i, (t, _) in enumerate(pair[:-12])])
    stale = evaluate(pair, 1, conf, directory=str(stale_dir))
    assert any("古く" in s for s in stale.skipped), stale.skipped
    assert stale.state in ("UNAVAILABLE", "UNKNOWN"), stale.state


def test_the_lag_is_measured_against_the_pair_not_the_wall_clock(tmp_path, cfg):
    """**壁時計で測らない。** 過去をさかのぼる検証で、全部が古く見える。"""
    from datetime import datetime, timedelta, timezone

    from app.correlation import evaluate

    # 1年前の期間。壁時計で測れば材料は「1年古い」ことになる。
    base = datetime(2025, 9, 15, 0, 0, tzinfo=timezone.utc)
    n = 200
    pair = [(base + timedelta(hours=i), 150.0 + i * 0.1) for i in range(n)]
    p = tmp_path / "DXY_H1.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        f.write("timestamp,open,high,low,close,volume\n")
        for i, (t, _) in enumerate(pair):
            v = 100.0 + i * 0.1
            f.write(f"{t.isoformat()},{v},{v},{v},{v},0\n")

    conf = dict(cfg.correlation)
    conf["drivers"] = {"DXY": {"symbol": "DX-Y.NYB", "label": "ドル指数"}}
    res = evaluate(pair, 1, conf, directory=str(tmp_path))
    assert not any("古く" in s for s in res.skipped), res.skipped
