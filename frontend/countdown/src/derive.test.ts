// derive.ts のテスト（npm test）。models.py のテスト（apps/countdown/tests.py）と同じ数え方になるかを見る。

import { describe, expect, it } from 'vitest'

import { countNote, derive, formatRemaining, remainingMs, yearsPassed } from './derive'

const TODAY = '2026-11-07'

describe('derive', () => {
  it('これからの予定は残り日数を数える', () => {
    const x = derive('2026-11-17', TODAY)
    expect([x.delta, x.countDays, x.weeks, x.extraDays, x.urgency]).toEqual([10, 10, 1, 3, 'later'])
    expect(countNote(x)).toBe('あと10日（1週と3日）')
  })

  it('週は必ず付ける（7日未満は 0週、ちょうどの週は日を省く）', () => {
    expect(countNote(derive('2026-11-12', TODAY))).toBe('あと5日（0週と5日）')
    expect(countNote(derive('2026-11-21', TODAY))).toBe('あと14日（2週）')
    expect(countNote(derive('2026-11-02', TODAY))).toBe('経過5日（0週と5日）')
    expect(countNote(derive('2026-10-31', TODAY))).toBe('経過7日（1週）')
  })

  it('7日以内は「もうすぐ」', () => {
    expect(derive('2026-11-14', TODAY).urgency).toBe('soon')
    expect(derive('2026-11-15', TODAY).urgency).toBe('later')
  })

  it('当日', () => {
    const x = derive(TODAY, TODAY)
    expect([x.delta, x.countDays, x.urgency, countNote(x)]).toEqual([0, 0, 'today', '本日'])
  })

  it('過ぎた出来事は経過日数と周年を数える', () => {
    const x = derive('2025-10-03', TODAY)
    expect([x.delta, x.countDays, x.years, x.urgency]).toEqual([-400, 400, 1, 'past'])
    expect(countNote(x)).toBe('経過400日（57週と1日・1周年経過）')
  })

  it('年をまたいでも日数がずれない（うるう年を含む）', () => {
    expect(derive('2028-03-01', '2028-02-28').delta).toBe(2)
    expect(derive('2027-03-01', '2027-02-28').delta).toBe(1)
  })
})

describe('yearsPassed', () => {
  it('記念日の前日はまだ迎えていない', () => {
    expect(yearsPassed('2020-11-08', TODAY)).toBe(5)
    expect(yearsPassed('2020-11-07', TODAY)).toBe(6)
  })

  it('未来と当日は 0', () => {
    expect(yearsPassed('2027-01-01', TODAY)).toBe(0)
    expect(yearsPassed(TODAY, TODAY)).toBe(0)
  })

  it('2月29日の出来事は、うるう年でない年は3月1日に迎える', () => {
    expect(yearsPassed('2024-02-29', '2025-02-28')).toBe(0)
    expect(yearsPassed('2024-02-29', '2025-03-01')).toBe(1)
  })
})

describe('リングの進み具合', () => {
  it('これからの予定：登録した日から当日まで', () => {
    // 10/28 に登録、11/17 が当日、今日 11/07 → 20 日のうち 10 日
    expect(derive('2026-11-17', TODAY, '2026-10-28T09:00:00+09:00').progress).toBeCloseTo(0.5)
  })

  it('当日に登録した当日の予定は 100%', () => {
    expect(derive(TODAY, TODAY, TODAY).progress).toBe(1)
  })

  it('過ぎた出来事：前の記念日から次の記念日まで', () => {
    const x = derive('2025-11-07', '2026-05-08')
    expect(x.progress).toBeGreaterThan(0.49)
    expect(x.progress).toBeLessThan(0.51)
    expect(x.progressLabel).toBe('1周年まであと183日')
  })
})

describe('残り時間', () => {
  const now = new Date(2026, 10, 7, 10, 15, 55) // 11/7 10:15:55（ブラウザの時刻）

  it('明日の予定は、その日の 0 時まで', () => {
    expect(formatRemaining(remainingMs('2026-11-08', 1, now))).toBe('13時間44分05秒')
  })

  it('当日の予定は、今日が終わるまで', () => {
    expect(formatRemaining(remainingMs(TODAY, 0, now))).toBe('13時間44分05秒')
  })
})
