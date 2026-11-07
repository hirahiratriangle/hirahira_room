// 一覧の1件。未来は残り日数、過去は経過日数を大きく出す。

import { Link } from 'react-router'

import type { CountEvent } from './api'
import { formatDate } from './format'
import { Icon } from './Icon'

export function EventCard({ event }: { event: CountEvent }) {
  const isPast = event.days_delta < 0
  const isToday = event.days_delta === 0

  return (
    <div className="card shadow-sm mb-3">
      <div className="card-body d-flex flex-wrap justify-content-between align-items-center">
        <div className="me-3">
          <h4 className="h5 mb-1">{event.name}</h4>
          <p className="text-muted small mb-1">{formatDate(event.date)}</p>
          {event.memo && <p className="text-muted small mb-1 cd-memo">{event.memo}</p>}
          {!isPast && event.notify && (
            <p className="small mb-0 cd-accent">
              <Icon name="envelope" className="me-1" />メール通知オン
            </p>
          )}
        </div>

        <div className="text-end">
          {isToday ? (
            <span className="badge bg-primary fs-6">本日</span>
          ) : (
            <>
              <div>
                <span className="fs-2 fw-bold cd-accent">{event.count_days}</span>
                <span className="text-muted">日</span>
              </div>
              <p className="text-muted small mb-0">{isPast ? pastNote(event) : upcomingNote(event)}</p>
            </>
          )}
          <div className="mt-2">
            <Link to={`/${event.id}/update/`} className="btn btn-sm btn-outline-primary me-1">編集</Link>
            <Link to={`/${event.id}/delete/`} className="btn btn-sm btn-outline-secondary">削除</Link>
          </div>
        </div>
      </div>
    </div>
  )
}

/** 「あと10日（1週と3日）」 */
function upcomingNote(event: CountEvent): string {
  let note = `あと${event.count_days}日`
  if (event.weeks) {
    note += `（${event.weeks}週${event.extra_days ? `と${event.extra_days}日` : ''}）`
  }
  return note
}

/** 「経過400日（1周年経過）」 */
function pastNote(event: CountEvent): string {
  return `経過${event.count_days}日${event.years_passed ? `（${event.years_passed}周年経過）` : ''}`
}
