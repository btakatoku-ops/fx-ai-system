// 期限の判定。**画面に出す瞬間にもう一度確かめるための小さな部品。**
//
// API が返した時点で有効でも、画面に出るまでに切れていることがある。
// ここは純粋な関数だけにしてあり、試験から直接呼べる。

/** 期限が切れているか。valid_until が無ければ切れていない扱い。 */
export function isExpired(validUntil: string | null, now = Date.now()): boolean {
  if (!validUntil) return false;
  const t = Date.parse(validUntil);
  // 読めない日時を「有効」と扱わない。**読めないなら切れているとみなす。**
  if (!Number.isFinite(t)) return true;
  return now >= t;
}

/** 残り秒。切れていれば0以下を返す。valid_until が無ければ null。 */
export function secondsRemaining(
  validUntil: string | null,
  now = Date.now()
): number | null {
  if (!validUntil) return null;
  const t = Date.parse(validUntil);
  if (!Number.isFinite(t)) return null;
  return Math.round((t - now) / 1000);
}

/** 金額の表示。**プラスは符号を付ける**（費用と利益を見間違えないため）。 */
export function yen(m: { amount: number; currency: string } | null): string {
  if (!m) return "—";
  const sign = m.amount >= 0 ? "+" : "−";
  return `${sign}${Math.abs(Math.round(m.amount)).toLocaleString("ja-JP")} 円`;
}
