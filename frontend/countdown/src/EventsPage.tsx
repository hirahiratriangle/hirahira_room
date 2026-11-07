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
// 一覧の上の道具：
//   ・絞り込みのタブ（すべて／これから／過ぎた／通知オン）
//   ・検索（名前とメモ）
//   ・並べ替え（日付が近い順／名前順／登録が新しい順）
// タブと並べ替えは、次に開いたときも同じになるよう、このブラウザに覚えておく。
//
// キーボード：N で新規登録、/ で検索へ、Esc でパネルを閉じる・検索を消す。

import { useEffect, useMemo, useRef, useState } from 'react'
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
type Sort = 'date' | 'name' | 'created'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'すべて' },
  { key: 'upcoming', label: 'これから' },
  { key: 'past', label: '過ぎた' },
  { key: 'notify', label: '通知オン' },
]

const SORTS: { key: Sort; label: string }[] = [
  { key: 'date', label: '日付が近い順' },
  { key: 'name', label: '名前順' },
  { key: 'created', label: '登録が新しい順' },
]

const EMPTY_INPUT: EventInput = { name: '', date: '', memo: '', notify: false }

// ---- タブと並べ替えをこのブラウザに覚えておく（使えないブラウザでは覚えないだけ） ----
const VIEW_KEY = 'countdown:view'

function loadView(): { filter: Filter; sort: Sort } {
  try {
    const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? '{}')
    return {
      filter: FILTERS.some((f) => f.key === saved.filter) ? saved.filter : 'all',
      sort: SORTS.some((s) => s.key === saved.sort) ? saved.sort : 'date',
    }
  } catch {
    return { filter: 'all', sort: 'date' }
  }
}

function saveView(view: { filter: Filter; sort: Sort }) {
  try {
    localStorage.setItem(VIEW_KEY, JSON.stringify(view))
  } catch {
    // 覚えられなくても、画面はそのまま使える
  }
}

const collator = new Intl.Collator('ja')

/** 並べ替え。「日付が近い順」は、これからの予定は近い順、過ぎた出来事は最近のものから */
function sortItems(items: Item[], sort: Sort, upcoming: boolean): Item[] {
  const list = [...items]
  if (sort === 'name') return list.sort((a, b) => collator.compare(a.name, b.name))
  if (sort === 'created') return list.sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id)
  return list.sort((a, b) => (upcoming ? a.date.localeCompare(b.date) : b.date.localeCompare(a.date)))
}

export function EventsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const { state, draft, clearDraft, highlight, reload, save, rename, remove } = useEvents()

  const createMatch = useMatch('/create')
  const updateMatch = useMatch('/:id/update')
  const deleteMatch = useMatch('/:id/delete')

  const [view, setView] = useState(loadView)
  const [query, setQuery] = useState('')
  const search = useRef<HTMLInputElement>(null)

  useEffect(() => saveView(view), [view])

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

  // ---- 絞り込み・検索・並べ替え ----
  const q = query.trim().toLowerCase()
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
    const hit = (e: Item) => !q || e.name.toLowerCase().includes(q) || e.memo.toLowerCase().includes(q)
    let upShown = up.filter(hit)
    let pastShown = ps.filter(hit)
    if (view.filter === 'past') upShown = []
    if (view.filter === 'upcoming') pastShown = []
    if (view.filter === 'notify') {
      upShown = upShown.filter((e) => e.notify)
      pastShown = []
    }
    return {
      upcoming: sortItems(upShown, view.sort, true),
      past: sortItems(pastShown, view.sort, false),
      counts,
    }
  }, [events, today, q, view])

  const changeView = (next: Partial<typeof view>) => withTransition(() => setView((v) => ({ ...v, ...next })))

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
      } else if (e.key === '/') {
        e.preventDefault()
        search.current?.focus()
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
        <div className="container px-4 px-md-5 py-4 text-center text-white">
          <h1 className="h3 fw-bold mb-2">カウントアップ＆ダウン</h1>
          <p className="mb-0 small">{today ? `本日 ${formatDate(today)}` : ' '}</p>
        </div>
      </header>

      <main className="container px-4 px-md-5 py-4 py-md-5">
        <div className="cd-toolbar">
          <div className="cd-tabs" role="tablist" aria-label="絞り込み">
            {FILTERS.map((f) => (
              <button
                key={f.key} type="button" role="tab"
                aria-selected={view.filter === f.key}
                className={`cd-tab ${view.filter === f.key ? 'is-active' : ''}`}
                onClick={() => changeView({ filter: f.key })}
              >
                {f.label}
                {ready && <span className="cd-tab-count">{counts[f.key]}</span>}
              </button>
            ))}
          </div>

          <div className="cd-toolbar-row">
            <div className="cd-search">
              <Icon name="search" className="cd-search-icon" />
              <input
                ref={search}
                type="search"
                className="form-control"
                placeholder="名前・メモで探す"
                aria-label="名前・メモで探す"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') {
                    setQuery('')
                    e.currentTarget.blur()
                  }
                }}
              />
              <kbd className="cd-kbd cd-search-kbd" aria-hidden="true">/</kbd>
            </div>
            <select
              className="form-select cd-sort"
              aria-label="並べ替え"
              value={view.sort}
              onChange={(e) => changeView({ sort: e.target.value as Sort })}
            >
              {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
            <Link to="/create/" className="btn btn-primary cd-new">
              <Icon name="plus" className="me-1" />新規登録
              <kbd className="cd-kbd ms-2" aria-hidden="true">N</kbd>
            </Link>
          </div>
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
            <p className="mb-0">{q ? `「${query.trim()}」に当てはまるものはありません。` : 'ここに出すものはありません。'}</p>
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
    <section className="mb-5">
      <h2 className="cd-section-title">
        <Icon name={icon} className="me-2 cd-accent" />{title}
      </h2>
      {children}
    </section>
  )
}
