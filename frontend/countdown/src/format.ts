// 日付の見せ方。テンプレートのころの {{ date|date:"Y年n月j日 (D)" }} と同じ見た目にする。

const weekday = new Intl.DateTimeFormat('ja-JP', { weekday: 'short' })

/** "2026-11-07" → "2026年11月7日 (土)" */
export function formatDate(iso: string): string {
  // new Date("2026-11-07") は UTC の0時として読まれ、時差で前の日になることがある。
  // 年・月・日に分けて、その土地の日付として作る
  const [y, m, d] = iso.split('-').map(Number)
  return `${y}年${m}月${d}日 (${weekday.format(new Date(y, m - 1, d))})`
}
