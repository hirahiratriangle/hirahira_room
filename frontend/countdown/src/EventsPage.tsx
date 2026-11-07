// countdown の画面（ひとつのページ）。
//
//   /countdown/events/               一覧
//   /countdown/events/create/        一覧の上に、新規登録のパネルを開く
//   /countdown/events/<id>/update/   一覧の上に、編集のパネルを開く
//   /countdown/events/<id>/delete/   以前の削除確認のページ。いまは一覧へ戻す（削除は一覧の「削除」から）
//
// どのアドレスでもこの部品が描くので、パネルを開け閉めしても一覧は描き直されない
// （絞り込み・検索・スクロールの位置がそのまま残る）。
//
// 一覧の上は、絞り込みのタブ（すべて／これから／過ぎた／通知オン）と新規登録だけ。
// 開いたときは、いつも「これから」のタブから始める（前に選んだタブは覚えない）。
// 並びは日数の少ない順に決めてある（これからの予定は近い順、過ぎた出来事は最近のものから）。
//
// キーボード：N で新規登録、Esc でパネルを閉じる。

import { useEffect, useMemo, useState } from 'react'
import { Link, useMatch, useNavigate } from 'react-router'

import type { EventInput } from './api'
import { derive } from './derive'
import { EventCard } from './EventCard'
import { EventPanel } from './EventPanel'
import { formatDate } from './format'
import { Icon } from './Icon'
import { Skeleton } from './Skeleton'
import { useToast } from './toast'
import { withTransition } from './transition'
import { useEvents, type Item } from './useEvents'
import { useNow } from './useNow'

type Filter = 'all' | 'upcoming' | 'past' | 'notify'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'すべて' },
  { key: 'upcoming', label: 'これから' },
  { key: 'past', label: '過ぎた' },
  { key: 'notify', label: '通知オン' },
]

const EMPTY_INPUT: EventInput = { name: '', date: '', memo: '', notify: false }

/**
 * 開いたときのタブ。いつも「これから」から始める。
 * 以前は前に選んだタブをブラウザに覚えていたが、「これから」以外が残っていると、
 * 開いたときにカードが1枚も出ず、データが消えたように見えることがあったのでやめた。
 */
const DEFAULT_FILTER: Filter = 'upcoming'

/** 選んでいるタブに出すものが無いときの言葉 */
const EMPTY_TEXT: Record<Filter, string> = {
  all: 'ここに出すものはありません。',
  upcoming: 'これからの予定はありません。',
  past: '過ぎた出来事はありません。',
  notify: '通知をオンにした予定はありません。',
}

/**
 * 日数の少ない順。これからの予定は残り日数の少ない（近い）順、
 * 過ぎた出来事は経過日数の少ない（最近の）順。同じ日なら先に登録したほうを上に。
 */
function byDays(items: Item[], upcoming: boolean): Item[] {
  return [...items].sort((a, b) =>
    (upcoming ? a.date.localeCompare(b.date) : b.date.localeCompare(a.date)) || a.id - b.id)
}

export function EventsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const { state, draft, clearDraft, highlight, reload, save, rename, remove } = useEvents()

  const createMatch = useMatch('/create')
  const updateMatch = useMatch('/:id/update')
  const deleteMatch = useMatch('/:id/delete')

  const [filter, setFilter] = useState<Filter>(DEFAULT_FILTER)

  // 以前の削除確認のアドレスで開かれたら、一覧へ戻す
  useEffect(() => {
    if (deleteMatch) navigate('/', { replace: true })
  }, [deleteMatch, navigate])

  const ready = state.status === 'ready' ? state : null
  const today = ready?.today ?? ''
  const events = useMemo(() => ready?.events ?? [], [ready])

  // 当日・明日の予定があるあいだだけ、1秒ごとに時計を進める
  const hasTicking = events.some((e) => today && [0, 1].includes(derive(e.date, today).delta))
  const now = useNow(hasTicking, () => void reload())

  // ---- 絞り込み ----
  const { upcoming, past, counts } = useMemo(() => {
    const up: Item[] = []
    const ps: Item[] = []
    for (const e of events) (e.date >= today ? up : ps).push(e)
    const counts: Record<Filter, number> = {
      all: events.length,
      upcoming: up.length,
      past: ps.length,
      notify: up.filter((e) => e.notify).length,
    }
    let upShown = up
    let pastShown = ps
    if (filter === 'past') upShown = []
    if (filter === 'upcoming') pastShown = []
    if (filter === 'notify') {
      upShown = up.filter((e) => e.notify)
      pastShown = []
    }
    return { upcoming: byDays(upShown, true), past: byDays(pastShown, false), counts }
  }, [events, today, filter])

  const changeFilter = (next: Filter) => withTransition(() => setFilter(next))

  // ---- 入力パネル ----
  const editId = updateMatch ? Number(updateMatch.params.id) : null
  const editing = editId !== null ? events.find((e) => e.id === editId) : undefined
  const panelOpen = !!createMatch || (editId !== null && !!editing && !editing.pending)

  // 編集のアドレスなのに、その予定が無い（他人のもの・削除済み・番号ちがい）
  useEffect(() => {
    if (ready && editId !== null && !editing) {
      toast.show('編集する予定が見つかりません。', { kind: 'danger' })
      navigate('/', { replace: true })
    }
  }, [ready, editId, editing, navigate, toast])

  const closePanel = () => {
    clearDraft()
    navigate('/')
    document.title = '一覧 - カウントアップ＆ダウン | HirahiraRoom'
  }

  const submitPanel = (input: EventInput) => {
    navigate('/')
    document.title = '一覧 - カウントアップ＆ダウン | HirahiraRoom'
    void save(createMatch ? null : editId, input)
  }

  // 保存に失敗して開き直したときは、そのとき入れた値から始める
  const panelDraft = draft && draft.id === (createMatch ? null : editId) ? draft : null
  const panelInitial: EventInput = panelDraft?.input
    ?? (editing ? { name: editing.name, date: editing.date, memo: editing.memo, notify: editing.notify } : EMPTY_INPUT)

  // ---- キーボード ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return
      const target = e.target as HTMLElement
      if (target.closest('input, textarea, select, [contenteditable], dialog')) return
      if (e.key === 'n' || e.key === 'N') {
        e.preventDefault()
        navigate('/create/')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])

  useEffect(() => {
    if (!panelOpen) document.title = '一覧 - カウントアップ＆ダウン | HirahiraRoom'
  }, [panelOpen])

  const nothingShown = ready && upcoming.length === 0 && past.length === 0

  return (
    <div className="cd-app">
      <header className="cd-header">
        <div className="cd-main cd-header-inner">
          <h1 className="cd-title">カウントアップ＆ダウン</h1>
          <p className="cd-today">{today ? `本日 ${formatDate(today)}` : '\u00a0'}</p>
        </div>
      </header>

      <main className="cd-main cd-body">
        <div className="cd-toolbar">
          <div className="cd-tabs" role="tablist" aria-label="絞り込み">
            {FILTERS.map((f) => (
              <button
                key={f.key} type="button" role="tab"
                aria-selected={filter === f.key}
                className={`cd-tab ${filter === f.key ? 'is-active' : ''}`}
                onClick={() => changeFilter(f.key)}
              >
                {f.label}
                {ready && <span className="cd-tab-count">{counts[f.key]}</span>}
              </button>
            ))}
          </div>
          <Link to="/create/" className="btn btn-primary btn-sm cd-new">
            <Icon name="plus" className="me-1" />新規登録
            <kbd className="cd-kbd ms-2" aria-hidden="true">N</kbd>
          </Link>
        </div>

        {state.status === 'error' && (
          <div className="alert alert-danger d-flex align-items-center gap-3">
            <span>{state.message}</span>
            <button type="button" className="btn btn-sm btn-outline-danger ms-auto" onClick={() => void reload()}>
              読み込み直す
            </button>
          </div>
        )}

        {state.status === 'loading' && <Skeleton />}

        {ready && events.length === 0 && (
          <div className="cd-empty">
            <p className="mb-3">まだ何も登録されていません。</p>
            <Link to="/create/" className="btn btn-primary">最初の出来事・予定を登録する</Link>
          </div>
        )}

        {ready && events.length > 0 && nothingShown && (
          <div className="cd-empty">
            <p className="mb-0">{EMPTY_TEXT[filter]}</p>
          </div>
        )}

        {ready && upcoming.length > 0 && (
          <Section icon="arrow-down" title="これからの予定（カウントダウン）">
            {upcoming.map((e) => (
              <EventCard
                key={e.id} event={e} today={today} now={now} highlighted={highlight === e.id}
                onRename={(name) => void rename(e.id, name)} onDelete={() => remove(e.id)}
              />
            ))}
          </Section>
        )}

        {ready && past.length > 0 && (
          <Section icon="arrow-up" title="過ぎた出来事（カウントアップ）">
            {past.map((e) => (
              <EventCard
                key={e.id} event={e} today={today} now={now} highlighted={highlight === e.id}
                onRename={(name) => void rename(e.id, name)} onDelete={() => remove(e.id)}
              />
            ))}
          </Section>
        )}
      </main>

      {ready && panelOpen && (
        <EventPanel
          key={createMatch ? 'create' : `update-${editId}`}
          mode={createMatch ? 'create' : 'update'}
          initial={panelInitial}
          errors={panelDraft?.errors ?? {}}
          today={today}
          createdAt={editing?.created_at}
          onSubmit={submitPanel}
          onClose={closePanel}
        />
      )}
    </div>
  )
}

function Section({ icon, title, children }: { icon: string; title: string; children: React.ReactNode }) {
  return (
    <section className="cd-section">
      <h2 className="cd-section-title">
        <Icon name={icon} className="me-2 cd-accent" />{title}
      </h2>
      {children}
    </section>
  )
}
