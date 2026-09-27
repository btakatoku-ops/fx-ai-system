"use client";

// 画面を定期的に読み直す。
//
// **なぜ要るか。** 判断の有効期限は60秒しかない。手で読み直すしかないと、
// 開きっぱなしの画面はほぼ常に「期限切れ」になる。かといって期限を延ばすと、
// 根拠が失われた BUY をそのまま出し続けることになるので、期限は触らない。
// **画面のほうを読み直す。**
//
// 気をつけていること。
//
// 1. **期限の判定は変えない。** ここでやるのは読み直しだけ。切れた表示は
//    切れたと出る。読み直しが止まっていても、古い値を新しく見せない。
// 2. **裏に回ったタブでは動かさない。** 見ていない画面のために26銘柄を
//    計算し続けるのは、待ち時間を長くするだけ。
// 3. **止められる。** 選んだ状態はこの端末に覚えておく。

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

const KEY = "fx.autorefresh";

export default function AutoRefresh({ seconds = 30 }: { seconds?: number }) {
  const router = useRouter();
  const [on, setOn] = useState(false);
  const [left, setLeft] = useState(seconds);
  const [ready, setReady] = useState(false);
  const busy = useRef(false);

  // 覚えている設定を読む。**読めなくても落とさない**（既定は入り）。
  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = window.localStorage.getItem(KEY);
    } catch {
      saved = null;
    }
    setOn(saved === null ? true : saved === "1");
    setReady(true);
  }, []);

  const flip = useCallback(() => {
    setOn((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(KEY, next ? "1" : "0");
      } catch {
        /* 保存できなくても動きは変えない */
      }
      setLeft(seconds);
      return next;
    });
  }, [seconds]);

  // 残り時間は**この中だけ**で数える。
  //
  // 以前は setLeft の更新関数の中で router.refresh() を呼んでいた。更新関数は
  // 描画の途中で走るので、**別の部品（Router）を描画中に更新する**ことになり、
  // React が警告を出していた（Cannot update a component while rendering a
  // different component）。動いてはいたが、描画の途中で外へ影響を出すのは
  // いつ壊れてもおかしくない。数えるのを普通の変数に移し、読み直しは
  // 更新関数の外で呼ぶ。
  useEffect(() => {
    if (!ready || !on) return;
    let remain = seconds;
    setLeft(remain);
    const id = window.setInterval(() => {
      remain -= 1;
      if (remain > 0) {
        setLeft(remain);
        return;
      }
      remain = seconds;
      setLeft(remain);
      // 裏に回っているあいだは数えるだけで読み直さない
      if (document.hidden || busy.current) return;
      busy.current = true;
      router.refresh();
      // 読み直しは非同期。**重ねて投げない**ように少し置く。
      window.setTimeout(() => {
        busy.current = false;
      }, 1500);
    }, 1000);
    return () => window.clearInterval(id);
  }, [ready, on, seconds, router]);

  return (
    <span className="autorefresh">
      <button type="button" className={`chip${on ? " on" : ""}`} onClick={flip}>
        {on ? `自動更新 入（あと ${left} 秒）` : "自動更新 切"}
      </button>
      <button
        type="button"
        className="chip"
        onClick={() => {
          setLeft(seconds);
          router.refresh();
        }}
      >
        いま読み直す
      </button>
    </span>
  );
}
