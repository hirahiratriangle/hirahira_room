// 予定・出来事の一覧と、その書きかえ（登録・編集・名前の変更・削除）。
//
// どの書きかえも「楽観的更新」で行う。サーバーの返事を待たずに、まず画面を変えてしまい、
// 返事が来たらサーバーの値で置きかえる。失敗したときだけ元に戻し、トーストで知らせる。
// 押した瞬間に画面が変わるので、待たされている感じがなくなる。
//
// 削除は確認を出さず、すぐ一覧から消して「元に戻す」付きのトーストを出す。
// サーバーへ DELETE を送るのは、トーストが消えるまで（UNDO_MS）待ってから。
// そのあいだに「元に戻す」が押されたら、送らずに一覧へ戻すだけで済む。
// 待っているあいだにタブが閉じられたら、閉じる間ぎわに送る（pagehide）。

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { api, ApiError, type CountEvent, type EventInput, type FieldErrors } from './api'
import { useToast } from './toast'
import { afterTransitions, withTransition } from './transition'

/** 一覧の1件。pending はサーバーの返事を待っているあいだ true */
export type Item = CountEvent & { pending?: boolean }

/** 保存に失敗した入力。入力パネルを開き直したとき、ここから欄を埋め直す */
export type Draft = { id: number | null; input: EventInput; errors: FieldErrors }

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; today: string; events: Item[] }

/** 削除を取り消せる時間（ミリ秒）。トーストもこの長さだけ出す */
export const UNDO_MS = 6000

// 登録中（まだサーバーが番号を振っていない）のものに付ける仮の番号。負の数にして本物と区別する
let nextTempId = -1

export function useEvents() {
  const toast = useToast()
  const navigate = useNavigate()
  const [state, setState] = useState<State>({ status: 'loading' })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [highlight, setHighlight] = useState<number | null>(null)

  // 取り消し待ちの削除。番号 → タイマーと消したもの
  const pendingDeletes = useRef(new Map<number, { timer: number; event: Item }>())

  // 楽観的更新の「元に戻す」のために、いまの一覧をいつでも読めるようにしておく
  const eventsRef = useRef<Item[]>([])
  if (state.status === 'ready') eventsRef.current = state.events

  /**
   * 一覧を書きかえる。animate なら View Transitions でなめらかにつなぐ。
   * 動きなしの変更も、先に頼んだ動きつきの変更が済むのを待ってから行う（順番を守るため。transition.ts）
   */
  const setEvents = useCallback((change: (events: Item[]) => Item[], animate = true) => {
    const run = () =>
      setState((s) => (s.status === 'ready' ? { ...s, events: change(s.events) } : s))
    if (animate) withTransition(run)
    else afterTransitions(run)
  }, [])

  /** 新しく入った・戻ってきたカードを少しのあいだ光らせる */
  const flash = useCallback((id: number) => {
    setHighlight(id)
    window.setTimeout(() => setHighlight((h) => (h === id ? null : h)), 1800)
  }, [])

  const load = useCallback(async () => {
    try {
      const data = await api.list()
      const events = [...data.upcoming, ...data.past].filter((e) => !pendingDeletes.current.has(e.id))
      setState({ status: 'ready', today: data.today, events })
    } catch (e) {
      setState({ status: 'error', message: e instanceof Error ? e.message : '読み込めませんでした。' })
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 取り消し待ちの削除は、タブを閉じる・ほかのページへ移る間ぎわに送りきる
  useEffect(() => {
    const flush = () => {
      for (const [id, { timer }] of pendingDeletes.current) {
        window.clearTimeout(timer)
        api.removeOnLeave(id)
      }
      pendingDeletes.current.clear()
    }
    window.addEventListener('pagehide', flush)
    return () => window.removeEventListener('pagehide', flush)
  }, [])

  /** 失敗を知らせ、「フォームに戻る」で入力パネルを開き直せるようにする */
  const failSave = useCallback((id: number | null, input: EventInput, err: unknown, verb: string) => {
    const errors = err instanceof ApiError ? err.fieldErrors : {}
    setDraft({ id, input, errors })
    toast.show(`「${input.name}」を${verb}できませんでした。`, {
      kind: 'danger',
      duration: 8000,
      action: { label: 'フォームに戻る', onClick: () => navigate(id === null ? '/create/' : `/${id}/update/`) },
    })
  }, [navigate, toast])

  /** 登録（id が null）と編集 */
  const save = useCallback(async (id: number | null, input: EventInput) => {
    if (state.status !== 'ready') return
    setDraft(null)

    if (id === null) {
      const tempId = nextTempId--
      setEvents((list) => [...list, { ...input, id: tempId, created_at: state.today, pending: true }])
      try {
        const saved = await api.create(input)
        setEvents((list) => list.map((e) => (e.id === tempId ? saved : e)), false)
        afterTransitions(() => flash(saved.id))
        toast.show(`「${saved.name}」を登録しました。`)
      } catch (err) {
        setEvents((list) => list.filter((e) => e.id !== tempId))
        failSave(null, input, err, '登録')
      }
      return
    }

    const before = eventsRef.current.find((e) => e.id === id)
    if (!before) return
    setEvents((list) => list.map((e) => (e.id === id ? { ...e, ...input, pending: true } : e)))
    try {
      const saved = await api.update(id, input)
      setEvents((list) => list.map((e) => (e.id === id ? saved : e)), false)
      afterTransitions(() => flash(id))
      toast.show(`「${saved.name}」を更新しました。`)
    } catch (err) {
      setEvents((list) => list.map((e) => (e.id === id ? before : e)))
      failSave(id, input, err, '更新')
    }
  }, [state, setEvents, flash, toast, failSave])

  /** 一覧の上で名前だけを書きかえる */
  const rename = useCallback(async (id: number, name: string) => {
    const before = eventsRef.current.find((e) => e.id === id)
    if (!before || before.name === name) return
    setEvents((list) => list.map((e) => (e.id === id ? { ...e, name, pending: true } : e)), false)
    try {
      const saved = await api.update(id, { name })
      setEvents((list) => list.map((e) => (e.id === id ? saved : e)), false)
      toast.show(`名前を「${saved.name}」にしました。`)
    } catch (err) {
      setEvents((list) => list.map((e) => (e.id === id ? before : e)), false)
      const why = err instanceof ApiError && err.fieldErrors.name ? err.fieldErrors.name.join(' ') : ''
      toast.show(`名前を変えられませんでした。${why}`, { kind: 'danger' })
    }
  }, [setEvents, toast])

  /** 取り消し待ちの削除を取りやめ、一覧へ戻す */
  const undo = useCallback((id: number) => {
    const pending = pendingDeletes.current.get(id)
    if (!pending) return
    window.clearTimeout(pending.timer)
    pendingDeletes.current.delete(id)
    setEvents((list) => [...list, pending.event])
    flash(id)
  }, [setEvents, flash])

  const remove = useCallback((id: number) => {
    const event = eventsRef.current.find((e) => e.id === id)
    if (!event || event.pending) return
    setEvents((list) => list.filter((e) => e.id !== id))

    const timer = window.setTimeout(async () => {
      pendingDeletes.current.delete(id)
      try {
        await api.remove(id)
      } catch (err) {
        // すでに消えていた（404）なら、それで目的は果たしている
        if (err instanceof ApiError && err.status === 404) return
        setEvents((list) => [...list, event])
        toast.show(`「${event.name}」を削除できませんでした。`, { kind: 'danger' })
      }
    }, UNDO_MS)
    pendingDeletes.current.set(id, { timer, event })

    toast.show(`「${event.name}」を削除しました。`, {
      kind: 'info',
      duration: UNDO_MS,
      pausable: false,
      action: { label: '元に戻す', onClick: () => undo(id) },
    })
  }, [setEvents, toast, undo])

  return {
    state,
    draft,
    clearDraft: () => setDraft(null),
    highlight,
    reload: load,
    save,
    rename,
    remove,
  }
}
