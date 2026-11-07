// トースト（画面の端に出て、少しすると消える知らせ）。
//
// どの部品からでも useToast() の show() で出せる。ボタン（「元に戻す」など）を付けられる。
// マウスを載せているあいだは消えない（読んでいる途中で消えないように）。

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

import { Icon } from './Icon'

export type ToastKind = 'success' | 'danger' | 'info'

export type ToastOptions = {
  kind?: ToastKind
  /** 表示する長さ（ミリ秒） */
  duration?: number
  action?: { label: string; onClick: () => void }
  /**
   * マウスを載せているあいだ止めるか（既定は止める）。
   * 「元に戻す」のトーストは止めない。裏で削除を送る時刻と、トーストが消える時刻を
   * そろえておかないと、消えたあとに「元に戻す」が押せてしまうため。
   */
  pausable?: boolean
}

type Toast = ToastOptions & { id: number; text: string }

type ToastApi = { show: (text: string, options?: ToastOptions) => void }

const ToastContext = createContext<ToastApi>({ show: () => {} })

export const useToast = () => useContext(ToastContext)

let nextId = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  const show = useCallback((text: string, options: ToastOptions = {}) => {
    // 同時に出しておくのは 3 つまで。古いものから消す
    setToasts((list) => [...list.slice(-2), { id: nextId++, text, ...options }])
  }, [])

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      {/* 読み上げソフトにも知らせる（aria-live） */}
      <div className="cd-toasts" aria-live="polite">
        {toasts.map((t) => <ToastItem key={t.id} toast={t} onDone={() => dismiss(t.id)} />)}
      </div>
    </ToastContext.Provider>
  )
}

function ToastItem({ toast, onDone }: { toast: Toast; onDone: () => void }) {
  const { text, kind = 'success', duration = 4000, action, pausable = true } = toast
  const [paused, setPaused] = useState(false)
  // 止めていたぶんを引いた残り時間
  const left = useRef(duration)
  const started = useRef(0)

  useEffect(() => {
    if (paused) return
    started.current = Date.now()
    const timer = window.setTimeout(onDone, left.current)
    return () => {
      window.clearTimeout(timer)
      left.current -= Date.now() - started.current
    }
  }, [paused, onDone])

  const icon = kind === 'danger' ? 'exclamation-circle' : kind === 'info' ? 'info-circle' : 'check-circle'

  return (
    <div
      className={`cd-toast cd-toast-${kind}`}
      role={kind === 'danger' ? 'alert' : 'status'}
      onMouseEnter={() => pausable && setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <Icon name={icon} className="cd-toast-icon" />
      <span className="cd-toast-text">{text}</span>
      {action && (
        <button
          type="button"
          className="cd-toast-action"
          onClick={() => {
            action.onClick()
            onDone()
          }}
        >
          {action.label}
        </button>
      )}
      <button type="button" className="cd-toast-close" aria-label="閉じる" onClick={onDone}>×</button>
      {/* 残り時間の細い帯。止めているあいだは帯も止まる */}
      <span
        className="cd-toast-timer"
        style={{ animationDuration: `${duration}ms`, animationPlayState: paused ? 'paused' : 'running' }}
      />
    </div>
  )
}
