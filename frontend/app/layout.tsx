import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FX 判断支援エンジン",
  description:
    "相場を分析して BUY / SELL / WAIT / NO_TRADE を返します。自動売買と発注は含みません。",
  manifest: "/manifest.webmanifest",
  icons: {
    icon: [{ url: "/icon-192.png", sizes: "192x192", type: "image/png" }],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
  // ホーム画面から開いたときに、Safari の枠を出さずに全画面で開く。
  appleWebApp: {
    capable: true,
    title: "FX 判断",
    statusBarStyle: "black-translucent",
  },
  // 電話番号らしき数字（価格やスコア）を Safari が勝手にリンクにしない
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // **画面いっぱいに広げる。** そのぶん、切り欠きとホームバーの内側に
  // 入らないよう、CSS 側で safe-area を見て余白を取る。
  viewportFit: "cover",
  themeColor: "#0f1217",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>
        <main>{children}</main>
      </body>
    </html>
  );
}
