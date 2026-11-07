// 進み具合のリング。真ん中に日数を大きく出す。
// これからの予定は「登録した日から当日まで」、過ぎた出来事は「次の記念日まで」の進み具合
// （計算は derive.ts）。色は急ぎぐあいで変わる（app.css の --cd-ring）。

import type { ReactNode } from 'react'

const SIZE = 84
const STROKE = 6
const R = (SIZE - STROKE) / 2
const LENGTH = 2 * Math.PI * R

type Props = { progress: number; label: string; children: ReactNode }

export function ProgressRing({ progress, label, children }: Props) {
  return (
    <div className="cd-ring" role="img" aria-label={label} title={label}>
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
        <circle className="cd-ring-track" cx={SIZE / 2} cy={SIZE / 2} r={R} strokeWidth={STROKE} />
        <circle
          className="cd-ring-bar"
          cx={SIZE / 2} cy={SIZE / 2} r={R} strokeWidth={STROKE}
          strokeDasharray={LENGTH}
          strokeDashoffset={LENGTH * (1 - progress)}
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
        />
      </svg>
      <div className="cd-ring-center">{children}</div>
    </div>
  )
}
