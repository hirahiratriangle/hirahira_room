// countdown の画面（React）のビルド設定。
//
// ビルドしたものは Django の static に出し、WhiteNoise がそのまま配る。
//   apps/countdown/static/countdown/app/app.js
//   apps/countdown/static/countdown/app/app.css
// 入れ物のテンプレート（countdown/app.html）が、この2つを決まった名前で読む。
//
// ファイル名は決まった名前にし、ハッシュを付けない。テンプレートが {% static %} で
// 名前を指して読むため。ほかの静的ファイル（css/mystyle.css など）と同じ扱いで、
// WhiteNoise は名前にハッシュの無いファイルを短い間（既定 60 秒）だけキャッシュさせる。
// デプロイしてしばらくすれば、ブラウザは新しい app.js を読む。
// ファイルを1つにまとめるのも同じ理由（分けると、テンプレートから指す名前が増える）。

import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const outDir = fileURLToPath(
  new URL('../../apps/countdown/static/countdown/app', import.meta.url),
)

export default defineConfig({
  plugins: [react()],
  build: {
    outDir,
    emptyOutDir: true,
    // 古いブラウザ向けの書きかえはしない（いまのブラウザが読める形のまま出す）
    target: 'es2022',
    rollupOptions: {
      input: fileURLToPath(new URL('./src/main.tsx', import.meta.url)),
      output: {
        entryFileNames: 'app.js',
        assetFileNames: 'app[extname]',
        codeSplitting: false,
      },
    },
  },
})
