// いまの時刻を、決まった間隔で配り直す。
//
// 当日・明日の予定があるあいだは 1 秒ごと（残り時間を秒まで減らして見せるため）。
// 無いときは 30 秒ごとで足りる（日付が変わったかを見るだけなので）。
// 日付が変わったら onDayChange を呼ぶ。一覧を読み直して、日数をサーバーの今日にそろえる。

import { useEffect, useRef, useState } from 'react'

const localDate = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`

export function useNow(everySecond: boolean, onDayChange: () => void): Date {
  const [now, setNow] = useState(() => new Date())
  const day = useRef(localDate(now))
  const callback = useRef(onDayChange)
  callback.current = onDayChange

  useEffect(() => {
    const timer = window.setInterval(() => {
      const next = new Date()
      setNow(next)
      const nextDay = localDate(next)
      if (nextDay !== day.current) {
        day.current = nextDay
        callback.current()
      }
    }, everySecond ? 1000 : 30_000)
    return () => window.clearInterval(timer)
  }, [everySecond])

  return now
}
