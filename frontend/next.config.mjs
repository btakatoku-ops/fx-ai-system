/** @type {import('next').NextConfig} */
const nextConfig = {
  // **127.0.0.1 で開いたときに画面が動かなくなるのを防ぐ。**
  //
  // Next 16 は、開発時の内部資源（/_next/hmr）へのアクセス元を既定で
  // localhost に限る。127.0.0.1 で開くと HMR の接続が拒否され、
  // **そのまま hydration も走らない**。画面は出るのに、押しても何も
  // 起きない、という分かりにくい壊れ方をする。実際そうなった。
  //
  // README も API も 127.0.0.1 で揃えているので、こちらを許可する。
  // 本番（next start）には関係しない設定。
  // 127.0.0.1 と localhost に加えて、同じ Wi-Fi の端末（iPhone など）から
  // 開けるように、このPCの LAN アドレスも許す。ここに無いと開発時は
  // HMR が拒否され、**画面は出るのに押しても何も起きない**状態になる。
  allowedDevOrigins: ["127.0.0.1", "localhost", "192.168.0.5"],

  // 画面は API を直接読む。Phase 1 では認証も秘密も持たない。
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000",
  },
};
export default nextConfig;
