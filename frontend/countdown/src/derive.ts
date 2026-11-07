// 日数の計算（画面用）。
//
// サーバーも同じ計算をして API で返している（apps/countdown/models.py のプロパティ）。
// 画面でも計算するのは、サーバーに聞く前に数字を出したい場面があるため。
//   ・入力パネルで日付を選んだ瞬間に「あと43日」を見せる
//   ・保存ボタンを押した瞬間に、サーバーの返事を待たずに一覧へ出す（楽観的更新）
// 「今日」はブラウザの時計ではなく、サーバーが返す today を使う。数え方が
// サーバーと食い違わないようにするため（ブラウザの時計が狂っていても日数はずれない）。
// 計算を変えるときは models.py と derive.test.ts もいっしょに直す。

/** "2026-11-07" → 1970-01-01 からの日数（時差の影響を受けないよう UTC で数える） */
export function toDayNumber(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return Date.UTC(y, m - 1, d) / 86_400_000
}

/** 年・月・日から "2026-11-07" を作る */
function isoOf(y: number, m: number, d: number): string {
  return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

function isLeap(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0
}

/**
 * その年の記念日。2月29日の出来事は、うるう年でない年は3月1日に迎える。
 * models.py の years_passed が（月, 日）の大小で比べているのと同じ数え方。
 */
function anniversaryIn(year: number, month: number, day: number): string {
  if (month === 2 && day === 29 && !isLeap(year)) return isoOf(year, 3, 1)
  return isoOf(year, month, day)
}

/** 過ぎた出来事の周年（何回目の記念日を迎えたか）。models.py の years_passed と同じ */
export function yearsPassed(date: string, today: string): number {
  if (toDayNumber(date) >= toDayNumber(today)) return 0
  const [y, m, d] = date.split('-').map(Number)
  const [ty, tm, td] = today.split('-').map(Number)
  let years = ty - y
  if (tm < m || (tm === m && td < d)) years -= 1
  return years
}

export type Urgency = 'today' | 'soon' | 'later' | 'past'

export type Derived = {
  /** 今日から見た日数差。未来なら正、過去なら負、当日は 0 */
  delta: number
  /** 画面に大きく出す日数（常に 0 以上） */
  countDays: number
  weeks: number
  extraDays: number
  years: number
  /** 急ぎぐあい。色分けに使う（soon は 7 日以内） */
  urgency: Urgency
  /** リングの進み具合（0〜1） */
  progress: number
  /** リングの説明（読み上げ・ツールチップ用） */
  progressLabel: string
}

/** 7 日以内を「もうすぐ」とする */
export const SOON_DAYS = 7

/**
 * 1件の表示に要る数をまとめて出す。
 * createdAt は登録日時（"2026-10-01T12:00:00+09:00" の先頭10文字を使う）。
 * 登録前の入力中など、まだ無いときは today を渡す。
 */
export function derive(date: string, today: string, createdAt: string = today): Derived {
  const delta = toDayNumber(date) - toDayNumber(today)
  const countDays = Math.abs(delta)
  const years = yearsPassed(date, today)
  const urgency: Urgency = delta === 0 ? 'today' : delta < 0 ? 'past' : delta <= SOON_DAYS ? 'soon' : 'later'

  let progress: number
  let progressLabel: string
  if (delta >= 0) {
    // これからの予定：登録した日から当日までのうち、どこまで来たか
    const start = Math.min(toDayNumber(createdAt), toDayNumber(today))
    const total = toDayNumber(date) - start
    progress = total <= 0 ? 1 : (toDayNumber(today) - start) / total
    progressLabel = `登録した日から当日まで ${Math.round(progress * 100)}% 進みました`
  } else {
    // 過ぎた出来事：前の記念日から次の記念日までのうち、どこまで来たか
    const [y, m, d] = date.split('-').map(Number)
    const last = years === 0 ? date : anniversaryIn(y + years, m, d)
    const next = anniversaryIn(y + years + 1, m, d)
    const span = toDayNumber(next) - toDayNumber(last)
    progress = (toDayNumber(today) - toDayNumber(last)) / span
    const left = toDayNumber(next) - toDayNumber(today)
    progressLabel = `${years + 1}周年まであと${left}日`
  }

  return {
    delta,
    countDays,
    weeks: Math.floor(countDays / 7),
    extraDays: countDays % 7,
    years,
    urgency,
    progress: Math.min(1, Math.max(0, progress)),
    progressLabel,
  }
}

/** 週と日。「1週と3日」「2週」「0週と5日」。週は 0 でも必ず出す */
export function weeksText(x: Derived): string {
  return `${x.weeks}週${x.extraDays ? `と${x.extraDays}日` : ''}`
}

/**
 * 日数の補足。これからの予定も過ぎた出来事も、必ず週を付ける。
 * 「あと10日（1週と3日）」「あと5日（0週と5日）」「経過400日（57週と1日・1周年経過）」「本日」
 */
export function countNote(x: Derived): string {
  if (x.delta === 0) return '本日'
  if (x.delta > 0) return `あと${x.countDays}日（${weeksText(x)}）`
  const years = x.years ? `・${x.years}周年経過` : ''
  return `経過${x.countDays}日（${weeksText(x)}${years}）`
}

/**
 * 当日・明日の予定の「残り時間」。ブラウザの時計で数える（秒単位で減っていく表示のため）。
 * 明日の予定は、その日の 0 時まで。当日の予定は、今日が終わる（翌日 0 時）まで。
 */
export function remainingMs(date: string, delta: number, now: Date): number {
  const [y, m, d] = date.split('-').map(Number)
  const target = new Date(y, m - 1, d + (delta === 0 ? 1 : 0))
  return Math.max(0, target.getTime() - now.getTime())
}

/** 49_445_000 → "13時間44分05秒" */
export function formatRemaining(ms: number): string {
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return `${h}時間${String(m).padStart(2, '0')}分${String(s).padStart(2, '0')}秒`
}
