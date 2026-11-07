// 登録・更新・削除のあとの知らせ。base.html の messages と同じ見た目（Bootstrap の alert）。

export type FlashState = { kind: 'success' | 'danger'; text: string }

export function Flash({ flash, onClose }: { flash: FlashState; onClose: () => void }) {
  return (
    <div className={`alert alert-${flash.kind} alert-dismissible`} role="status">
      {flash.text}
      <button type="button" className="btn-close" aria-label="閉じる" onClick={onClose} />
    </div>
  )
}
