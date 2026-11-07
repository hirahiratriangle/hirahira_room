// 削除の確認。ブラウザの <dialog> を使う。
// showModal() で開くと、後ろの画面は触れなくなり、Esc で閉じられる（Esc はキャンセル扱い）。

import { useEffect, useRef, useState } from 'react'

import type { CountEvent } from './api'
import { formatDate } from './format'

type Props = {
  event: CountEvent
  onConfirm: () => Promise<void>
  onCancel: () => void
}

export function DeleteDialog({ event, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const dialog = ref.current
    if (dialog && !dialog.open) dialog.showModal()
    return () => dialog?.close()
  }, [])

  async function confirm() {
    setBusy(true)
    await onConfirm()
  }

  return (
    <dialog
      ref={ref}
      className="cd-dialog shadow"
      aria-labelledby="cd-delete-title"
      onCancel={(e) => {
        e.preventDefault()
        if (!busy) onCancel()
      }}
    >
      {/* card を <dialog> に直接付けると、card の display: flex で縦いっぱいに伸びる。中に入れる */}
      <div className="card"><div className="card-body">
        <p className="mb-1">以下のデータを削除します。よろしいですか？</p>
        <h2 id="cd-delete-title" className="h5 mb-1">{event.name}</h2>
        <p className="text-muted small mb-3">{formatDate(event.date)}</p>
        <div className="d-flex gap-2">
          <button type="button" className="btn btn-danger" onClick={confirm} disabled={busy}>
            {busy ? '削除しています…' : '削除する'}
          </button>
          <button type="button" className="btn btn-outline-secondary" onClick={onCancel} disabled={busy}>
            キャンセル
          </button>
        </div>
      </div></div>
    </dialog>
  )
}
