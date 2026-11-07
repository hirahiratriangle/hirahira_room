// 新規登録と編集。/create/ なら新規、/<id>/update/ なら編集。
// 保存できたら一覧へ戻り、一覧に知らせを出す。入力のまちがいは欄の下に出す。

import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { api, ApiError, type EventInput, type FieldErrors } from './api'
import { Flash, type FlashState } from './Flash'
import { Header } from './Header'
import { Icon } from './Icon'

const EMPTY: EventInput = { name: '', date: '', memo: '', notify: false }

export function EventForm() {
  const { id } = useParams()
  const isEdit = id !== undefined
  const formTitle = isEdit ? '編集' : '新規登録'
  const navigate = useNavigate()

  const [input, setInput] = useState<EventInput>(EMPTY)
  const [loaded, setLoaded] = useState(!isEdit)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [flash, setFlash] = useState<FlashState | null>(null)
  const [saving, setSaving] = useState(false)

  // 編集なら、いまの値を読んで欄に入れる
  useEffect(() => {
    if (!isEdit) return
    let alive = true
    api.get(Number(id))
      .then((event) => {
        if (!alive) return
        setInput({ name: event.name, date: event.date, memo: event.memo, notify: event.notify })
        setLoaded(true)
      })
      .catch((e: unknown) => {
        if (!alive) return
        const text = e instanceof Error ? e.message : '読み込めませんでした。'
        navigate('/', { replace: true, state: { flash: { kind: 'danger', text } } })
      })
    return () => {
      alive = false
    }
  }, [id, isEdit, navigate])

  function set<K extends keyof EventInput>(key: K, value: EventInput[K]) {
    setInput((prev) => ({ ...prev, [key]: value }))
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setSaving(true)
    setErrors({})
    setFlash(null)
    try {
      const saved = isEdit ? await api.update(Number(id), input) : await api.create(input)
      const text = `「${saved.name}」を${isEdit ? '更新' : '登録'}しました。`
      navigate('/', { state: { flash: { kind: 'success', text } } })
    } catch (err) {
      if (err instanceof ApiError) setErrors(err.fieldErrors)
      setFlash({ kind: 'danger', text: `${isEdit ? '更新' : '登録'}に失敗しました。` })
      setSaving(false)
    }
  }

  return (
    <>
      <Header title={`出来事・予定の${formTitle}`} tabTitle={formTitle} />

      <div className="container px-5 py-5">
        <div className="row justify-content-center">
          <div className="col-md-8 col-lg-6">
            {flash && <Flash flash={flash} onClose={() => setFlash(null)} />}
            {errors.non_field_errors?.map((m) => <p key={m} className="text-danger small">{m}</p>)}

            {!loaded ? (
              <p className="text-muted small">読み込んでいます…</p>
            ) : (
              <form onSubmit={handleSubmit} noValidate>
                <div className="mb-3">
                  <label className="form-label" htmlFor="cd-name">名前</label>
                  <input
                    id="cd-name" className="form-control" maxLength={50} required
                    placeholder="例）結婚記念日、資格試験"
                    value={input.name} onChange={(e) => set('name', e.target.value)}
                  />
                  <FieldError messages={errors.name} />
                </div>

                <div className="mb-3">
                  <label className="form-label" htmlFor="cd-date">日付</label>
                  <input
                    id="cd-date" type="date" className="form-control" required
                    value={input.date} onChange={(e) => set('date', e.target.value)}
                  />
                  <FieldError messages={errors.date} />
                </div>

                <div className="mb-3">
                  <label className="form-label" htmlFor="cd-memo">メモ</label>
                  <textarea
                    id="cd-memo" className="form-control" rows={3} placeholder="メモ（任意）"
                    value={input.memo} onChange={(e) => set('memo', e.target.value)}
                  />
                  <FieldError messages={errors.memo} />
                </div>

                <p className="text-muted small">
                  今日より後の日付は残り日数のカウントダウン、今日より前の日付は経過日数のカウントアップになります。
                </p>

                <div className="card mb-4">
                  <div className="card-header bg-light">
                    <Icon name="envelope" className="me-1 cd-accent" /> メール通知
                  </div>
                  <div className="card-body">
                    <div className="form-check mb-3">
                      <input
                        id="cd-notify" type="checkbox" className="form-check-input"
                        checked={input.notify} onChange={(e) => set('notify', e.target.checked)}
                      />
                      <label className="form-check-label" htmlFor="cd-notify">メールで知らせる</label>
                      <FieldError messages={errors.notify} />
                    </div>
                    <p className="text-muted small mb-0">
                      毎日0時に、通知をオンにしたこれからの予定を1通にまとめて、
                      ログインに使っているメールアドレス宛にお送りします。
                      過ぎた出来事には送られません。
                    </p>
                  </div>
                </div>

                <div className="d-flex gap-2">
                  <button type="submit" className="btn btn-primary" disabled={saving}>
                    {saving ? '保存しています…' : '保存'}
                  </button>
                  <Link to="/" className="btn btn-outline-secondary">
                    <Icon name="list" /> 一覧に戻る
                  </Link>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

function FieldError({ messages }: { messages?: string[] }) {
  if (!messages?.length) return null
  return (
    <>
      {messages.map((m) => <div key={m} className="text-danger small">{m}</div>)}
    </>
  )
}
