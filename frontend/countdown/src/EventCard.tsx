// 一覧の1件。
//
// ・真ん中に日数の入ったリング。色は急ぎぐあい（当日・7日以内・それより先・過ぎた）で変わる
// ・当日と明日の予定は、残り時間を秒まで刻んで見せる（now が1秒ごとに変わる）
// ・名前をクリックすると、その場で書きかえられる（Enter で保存、Esc でやめる）
// ・削除はすぐ消える。取り消しはトーストの「元に戻す」で（useEvents.ts）
// ・view-transition-name を付けて、増える・消える・並びが変わるときに動きでつなぐ（transition.ts）

import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'

import { countNote, derive, formatRemaining, remainingMs } from './derive'
import { formatDate } from './format'
import { Icon } from './Icon'
import { ProgressRing } from './ProgressRing'
import type { Item } from './useEvents'

type Props = {
  event: Item
  today: string
  now: Date
  highlighted: boolean
  onRename: (name: string) => void
  onDelete: () => void
}

/** view-transition-name に使える名前（仮の番号は負なので、そのままでは使えない） */
const transitionName = (id: number) => `cd-card-${id < 0 ? `tmp${-id}` : id}`

export function EventCard({ event, today, now, highlighted, onRename, onDelete }: Props) {
  const x = derive(event.date, today, event.created_at)
  const ticking = x.delta === 0 || x.delta === 1

  const classes = [
    'card', 'cd-card', `cd-card--${x.urgency}`,
    event.pending && 'cd-card--pending',
    highlighted && 'cd-card--highlight',
  ].filter(Boolean).join(' ')

  return (
    <article className={classes} style={{ viewTransitionName: transitionName(event.id) }} aria-busy={event.pending}>
      <div className="card-body cd-card-body">
        <div className="cd-card-main">
          <div className="d-flex align-items-center gap-2 flex-wrap">
            <EditableName name={event.name} disabled={!!event.pending} onSave={onRename} />
            {x.urgency === 'soon' && <span className="cd-pill cd-pill--soon">もうすぐ</span>}
          </div>
          <p className="cd-date">
            {formatDate(event.date)}
            {x.delta >= 0 && event.notify && (
              <span className="cd-accent ms-2" title="メール通知オン">
                <Icon name="envelope" className="me-1" />通知オン
              </span>
            )}
          </p>
          {/* 残り日数と週（あと19日（2週と5日））。広い画面ではリングの横（cd-note）に出し、
              スマホではリングの横に入りきらないので、ここ（日付の下）に出す */}
          {x.delta !== 0 && <p className="cd-note-inline"><NoteText text={countNote(x)} /></p>}
          {event.memo && <p className="cd-muted cd-memo">{event.memo}</p>}
          {ticking && (
            <p className="cd-tick mb-0" aria-live="off">
              <Icon name="hourglass-half" className="me-1" />
              {x.delta === 0 ? '今日が終わるまで ' : '当日まで あと '}
              <span className="cd-tick-time">{formatRemaining(remainingMs(event.date, x.delta, now))}</span>
            </p>
          )}
        </div>

        {/* 右側：日数の補足と操作、その右にリング */}
        <div className="cd-card-side">
          <div className="cd-card-meta">
            <p className="cd-muted cd-note">{x.delta === 0 ? '当日です' : countNote(x)}</p>
            <div className="cd-card-actions">
              {event.pending ? (
                <span className="cd-muted small"><span className="cd-spinner" /> 保存しています</span>
              ) : (
                <>
                  <Link to={`/${event.id}/update/`} className="btn btn-sm btn-outline-primary">編集</Link>
                  <button type="button" className="btn btn-sm btn-outline-secondary" onClick={onDelete}>削除</button>
                </>
              )}
            </div>
          </div>
          <ProgressRing progress={x.progress} label={x.progressLabel}>
            {x.delta === 0 ? (
              <span className="cd-ring-today">本日</span>
            ) : (
              <>
                <span className={`cd-ring-days ${x.countDays >= 1000 ? 'is-long' : ''}`}>{x.countDays}</span>
                <span className="cd-ring-unit">日</span>
              </>
            )}
          </ProgressRing>
        </div>
      </div>
    </article>
  )
}

/** 名前。クリックすると入力欄になる */
function EditableName({ name, disabled, onSave }: { name: string; disabled: boolean; onSave: (name: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(name)
  const input = useRef<HTMLInputElement>(null)
  // Esc でやめた直後に、入力欄が消えることで blur（＝保存）が続いて起きることがある。二度目は無視する
  const finished = useRef(false)

  useEffect(() => {
    if (editing) input.current?.select()
  }, [editing])

  function finish(save: boolean) {
    if (finished.current) return
    finished.current = true
    setEditing(false)
    const trimmed = value.trim()
    if (save && trimmed && trimmed.length <= 50 && trimmed !== name) onSave(trimmed)
    else setValue(name)
  }

  if (editing) {
    return (
      <input
        ref={input}
        className="form-control form-control-sm cd-name-input"
        aria-label="名前"
        maxLength={50}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => finish(true)}
        onKeyDown={(e) => {
          // 変換中の Enter は確定のためのもの。保存しない
          if (e.key === 'Enter' && !e.nativeEvent.isComposing) finish(true)
          if (e.key === 'Escape') {
            e.preventDefault()
            finish(false)
          }
        }}
      />
    )
  }

  return (
    <h3 className="cd-name-heading">
      <button
        type="button"
        className="cd-name"
        disabled={disabled}
        title="クリックして名前を書きかえる"
        onClick={() => {
          setValue(name)
          finished.current = false
          setEditing(true)
        }}
      >
        {name}
      </button>
    </h3>
  )
}

/**
 * 日数の補足を、「・」のところでだけ折り返せるようにして出す。
 * 「経過400日（57週と1日・1周年経過）」が狭い画面で「1周年経 / 過）」と切れないように。
 */
function NoteText({ text }: { text: string }) {
  const parts = text.split('・')
  return (
    <>
      {parts.map((part, i) => (
        <span key={i} className="cd-nowrap">{i > 0 ? `・${part}` : part}</span>
      ))}
    </>
  )
}
