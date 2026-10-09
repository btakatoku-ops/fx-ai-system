// ダッシュボード（携帯の公開ページと同じ 1 枚）。バックエンドの /dashboard を、この画面と
// 同じ場所から開けるように中継する。**作り方はバックエンドに 1 つだけ**（app/dashboard_page.py）。
import { API_BASE } from "@/lib/api";

export async function GET() {
  try {
    const res = await fetch(`${API_BASE}/dashboard`, { cache: "no-store" });
    const html = await res.text();
    return new Response(html, {
      status: res.status,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  } catch (err) {
    const msg = String(err).replace(/[<>&]/g, "");
    return new Response(
      `<!doctype html><meta charset="utf-8"><title>FX ボード</title>` +
        `<body style="background:#060a11;color:#d5e1ef;font-family:sans-serif;padding:20px">` +
        `<p>API に繋がりません。バックエンドを起動してください。</p><p style="color:#7f8ea5">${msg}</p>` +
        `<p><a style="color:#39c2f2" href="/">分析の画面へ戻る</a></p></body>`,
      { status: 502, headers: { "Content-Type": "text/html; charset=utf-8" } },
    );
  }
}
