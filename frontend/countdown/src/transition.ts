// 画面の描きかえを、ブラウザの View Transitions でなめらかにつなぐ。
//
// document.startViewTransition は、描きかえる前と後の画面を撮って、そのあいだを動きでつなぐ。
// カードに view-transition-name（EventCard.tsx）を付けてあるので、カードごとに
// 増える・消える・並びが変わる動きになる。動きの中身は app.css の ::view-transition-*。
//
// React は描きかえをまとめて後回しにすることがあるので、flushSync で
// 「この関数の中の変更は、いま描ききる」と指示する。ブラウザが撮るのはその後の画面になる。
// 動きを減らす設定（prefers-reduced-motion）のときと、対応していないブラウザでは、ふつうに描きかえる。
//
// 注意：startViewTransition に渡した変更は、すぐには行われない（ブラウザが今の画面を
// 撮り終えた次の描画で行われる）。そのあいだに別の変更を先に済ませると、順番が入れかわる。
// たとえば「仮の1件を足す（動きあり）」より先に「仮の1件をサーバーの返事で置きかえる」が
// 走ると、置きかえる相手が見つからない。そこで、動きなしの変更は afterTransitions で
// 待っている動きの変更が済んでから行う。

import { flushSync } from 'react-dom'

const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

// いちばん最近の、動きつきの変更が済む時
let lastUpdate: Promise<unknown> = Promise.resolve()

// 動きが始まらないときに、変更だけ先に済ませるまでの待ち時間（ミリ秒）
const FALLBACK_MS = 400

export function withTransition(update: () => void) {
  if (!document.startViewTransition || reduceMotion() || document.hidden) {
    update()
    return
  }

  // ブラウザが描画を止めている（タブが裏に回った・埋め込みの画面が隠れているなど）と、
  // startViewTransition に渡した変更はいつまでも行われない。それではデータが画面に届かない
  // （保存中の印が消えない・元に戻らない）。そこで、しばらく待っても行われなければ、
  // 動きはあきらめて変更だけを行う。どちらが先でも、変更は1回だけ行う。
  let ran = false
  let finish = () => {}
  const done = new Promise<void>((resolve) => (finish = resolve))
  const runOnce = () => {
    if (ran) return
    ran = true
    flushSync(update)
    finish()
  }

  const transition = document.startViewTransition(runOnce)
  const fallback = window.setTimeout(() => {
    transition.skipTransition()
    runOnce()
  }, FALLBACK_MS)
  void done.then(() => window.clearTimeout(fallback))
  // 動きが飛ばされたときの失敗は気にしない（変更は runOnce で必ず行われる）
  transition.updateCallbackDone.catch(() => {})
  transition.finished.catch(() => {})

  // 前の変更が済んでから、この変更が済むまでを「最近の変更」とする（順番を守るため）
  lastUpdate = Promise.all([lastUpdate, done])
}

/** 待っている動きつきの変更が済んでから行う */
export function afterTransitions(update: () => void) {
  void lastUpdate.then(update)
}
