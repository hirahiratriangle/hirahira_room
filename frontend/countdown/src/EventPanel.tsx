// 登録・編集の入力パネル。一覧の上に重ねて開く（ページは移らない）。
//
// 広い画面では右から、スマホでは下からせり上がる（app.css の .cd-sheet）。
// ブラウザの <dialog> を showModal() で開くので、後ろの一覧は触れなくなり、Esc で閉じる。
// 背景（パネルの外）をクリックしても閉じる。
//
// 日付を選んだ瞬間に「あと43日」などの見通しを出す（derive.ts。サーバーには聞かない）。
// 保存を押したら、すぐパネルを閉じて一覧に反映する（楽観的更新。useEvents.ts）。
// そのため、空欄などのまちがいは閉じる前にここで確かめる。サーバーが受け付けなかったときは
// トーストの「フォームに戻る」で開き直すと、入れた値とメッセージが残っている（draft）。

import { useEffect, useRef, useState, type FormEvent } from 'react'

import type { EventInput, FieldErrors } from './api'
import { countNote, derive } from './derive'
import { formatDate } from './format'
import { Icon } from './Icon'
import { ProgressRing } from './ProgressRing'

type Props = {
  mode: 'create' | 'update'
  initial: EventInput
  errors: FieldErrors
  today: string
  createdAt?: string
  onSubmit: (input: EventInput) => void
  onClose: () => void
}

export function EventPanel({ mode, initial, errors: serverErrors, today, createdAt, onSubmit, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const [input, setInput] = useState<EventInput>(initial)
  const [errors, setErrors] = useState<FieldErrors>(serverErrors)
  const title = mode === 'create' ? '新規登録' : '編集'

  useEffect(() => {
    const dialog = ref.current
    if (dialog && !dialog.open) dialog.showModal()
    // showModal() は最初のボタン（×）に入力の的を移してしまう。名前の欄に移し直す
    nameRef.current?.focus()
    document.title = `${title} - カウントアップ＆ダウン | HirahiraRoom`
    return () => dialog?.close()
  }, [title])

  function set<K extends keyof EventInput>(key: K, value: EventInput[K]) {
    setInput((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const name = input.name.trim()
    const found: FieldErrors = {}
    if (!name) found.name = ['名前を入れてください。']
    if (!input.date) found.date = ['日付を選んでください。']
    if (found.name || found.date) {
      setErrors(found)
      return
    }
    onSubmit({ ...input, name })
  }

  // 日付を選んだら、その場で数えて見せる
  const preview = input.date ? derive(input.date, today, createdAt ?? today) : null

  return (
    <dialog
      ref={ref}
      className="cd-sheet"
      aria-labelledby="cd-panel-title"
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
      // パネルの外（::backdrop）をクリックしたときは、dialog 自身がクリックされたことになる
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <form className="cd-sheet-body" onSubmit={handleSubmit} noValidate>
        <div className="cd-sheet-head">
          <h2 id="cd-panel-title" className="h5 mb-0">出来事・予定の{title}</h2>
          <button type="button" className="cd-icon-button" aria-label="閉じる" onClick={onClose}>×</button>
        </div>

        <div className="cd-sheet-scroll">
          {errors.non_field_errors?.map((m) => <p key={m} className="text-danger small">{m}</p>)}

          <div className="mb-3">
            <label className="form-label" htmlFor="cd-name">名前</label>
            <input
              ref={nameRef}
              id="cd-name" className={`form-control ${errors.name ? 'is-invalid' : ''}`} maxLength={50}
              placeholder="例）結婚記念日、資格試験"
              value={input.name} onChange={(e) => set('name', e.target.value)}
            />
            <FieldError messages={errors.name} />
          </div>

          <div className="mb-3">
            <label className="form-label" htmlFor="cd-date">日付</label>
            <input
              id="cd-date" type="date" className={`form-control ${errors.date ? 'is-invalid' : ''}`}
              value={input.date} onChange={(e) => set('date', e.target.value)}
            />
            <FieldError messages={errors.date} />
          </div>

          {/* 入力中の見通し。日付を変えるたびに描きかわる */}
          <div className={`cd-preview cd-card--${preview?.urgency ?? 'later'}`} aria-live="polite">
            {preview ? (
              <>
                <ProgressRing progress={preview.progress} label={preview.progressLabel}>
                  {preview.delta === 0
                    ? <span className="cd-ring-today">本日</span>
                    : <><span className={`cd-ring-days ${preview.countDays >= 1000 ? 'is-long' : ''}`}>{preview.countDays}</span><span className="cd-ring-unit">日</span></>}
                </ProgressRing>
                <div>
                  <div className="fw-bold">{formatDate(input.date)}</div>
                  <div>{preview.delta === 0 ? '当日です' : countNote(preview)}</div>
                  <div className="cd-muted small">
                    {preview.delta > 0 ? 'カウントダウン' : preview.delta < 0 ? 'カウントアップ' : ''}
                    {/* 新規の予定は登録日が今日なので、進み具合はいつも 0%。言わずにおく */}
                    {(preview.delta < 0 || (mode === 'update' && preview.delta > 0)) && ` ・ ${preview.progressLabel}`}
                  </div>
                </div>
              </>
            ) : (
              <span className="cd-muted small">日付を選ぶと、残り日数・経過日数をここに出します。</span>
            )}
          </div>

          <div className="mb-3">
            <label className="form-label" htmlFor="cd-memo">メモ</label>
            <textarea
              id="cd-memo" className="form-control" rows={3} placeholder="メモ（任意）"
              value={input.memo} onChange={(e) => set('memo', e.target.value)}
            />
            <FieldError messages={errors.memo} />
          </div>

          <div className="cd-notify">
            <div className="form-check form-switch mb-1">
              <input
                id="cd-notify" type="checkbox" role="switch" className="form-check-input"
                checked={input.notify} onChange={(e) => set('notify', e.target.checked)}
              />
              <label className="form-check-label" htmlFor="cd-notify">
                <Icon name="envelope" className="me-1 cd-accent" />メールで知らせる
              </label>
            </div>
            <p className="cd-muted small mb-0">
              毎日0時に、通知をオンにしたこれからの予定を1通にまとめて、
              ログインに使っているメールアドレス宛にお送りします。過ぎた出来事には送られません。
            </p>
          </div>
        </div>

        <div className="cd-sheet-foot">
          <button type="button" className="btn btn-outline-secondary" onClick={onClose}>キャンセル</button>
          <button type="submit" className="btn btn-primary">保存</button>
        </div>
      </form>
    </dialog>
  )
}

function FieldError({ messages }: { messages?: string[] }) {
  if (!messages?.length) return null
  return <>{messages.map((m) => <div key={m} className="invalid-feedback d-block">{m}</div>)}</>
}
