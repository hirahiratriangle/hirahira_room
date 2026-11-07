// 一覧。これからの予定（カウントダウン）と過ぎた出来事（カウントアップ）を2段に並べる。
// 並べ方と日数はサーバーが決めて返すので、ここでは描くだけ。
//
// /<id>/delete/ で開いたときは、一覧の上に削除の確認（DeleteDialog）を出す。

import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'

import { api, ApiError, type CountEvent, type EventList as EventListData } from './api'
import { DeleteDialog } from './DeleteDialog'
import { EventCard } from './EventCard'
import { Flash, type FlashState } from './Flash'
import { formatDate } from './format'
import { Icon } from './Icon'
import { Header } from './Header'

export function EventList() {
  const navigate = useNavigate()
  const location = useLocation()
  const { id } = useParams()
  const [data, setData] = useState<EventListData | null>(null)
  const [error, setError] = useState('')
  // 登録・更新・削除のあとに出す知らせ。ほかの画面から移ってきたときは、その画面が渡す
  const [flash, setFlash] = useState<FlashState | null>(
    (location.state as { flash?: FlashState } | null)?.flash ?? null,
  )

  const load = useCallback(async () => {
    try {
      setData(await api.list())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '読み込めませんでした。')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 知らせは一度見せたら、戻る・進むで再び出ないよう履歴から消しておく
  useEffect(() => {
    if (location.state) navigate(location.pathname, { replace: true, state: null })
  }, [location.state, location.pathname, navigate])

  const all = data ? [...data.upcoming, ...data.past] : []
  const deleting = id ? all.find((e) => e.id === Number(id)) : undefined

  // /<id>/delete/ を開いたのに、その予定が一覧に無い（他人のもの・削除済み）
  useEffect(() => {
    if (data && id && !deleting) {
      setFlash({ kind: 'danger', text: '削除する予定が見つかりません。' })
      navigate('/', { replace: true })
    }
  }, [data, id, deleting, navigate])

  async function handleDelete(event: CountEvent) {
    try {
      await api.remove(event.id)
      setFlash({ kind: 'success', text: `「${event.name}」を削除しました。` })
    } catch (e) {
      // すでに消えていた（404）なら、一覧を読み直せば合う
      const text = e instanceof ApiError && e.status === 404
        ? `「${event.name}」はすでに削除されています。`
        : '削除に失敗しました。'
      setFlash({ kind: 'danger', text })
    }
    navigate('/', { replace: true })
    await load()
  }

  return (
    <>
      <Header title="カウントアップ＆ダウン" tabTitle="一覧">
        <p className="mb-0 small">{data ? `本日 ${formatDate(data.today)}` : ' '}</p>
      </Header>

      <div className="container px-5 py-5">
        {flash && <Flash flash={flash} onClose={() => setFlash(null)} />}

        <div className="d-flex justify-content-between align-items-center mb-4">
          <h2 className="h5 mb-0 cd-accent">登録した出来事・予定</h2>
          <Link to="/create/" className="btn btn-primary btn-sm">
            <Icon name="plus" className="me-1" />新規登録
          </Link>
        </div>

        {error && <p className="text-danger">{error}</p>}
        {!data && !error && <p className="text-muted small">読み込んでいます…</p>}

        {data && (
          <>
            <section className="mb-5">
              <h3 className="h6 text-muted mb-3">
                <Icon name="arrow-down" className="me-2 cd-accent" />これからの予定（カウントダウン）
              </h3>
              {data.upcoming.length === 0
                ? <p className="text-muted small">これからの予定はまだありません。</p>
                : data.upcoming.map((e) => <EventCard key={e.id} event={e} />)}
            </section>

            <section>
              <h3 className="h6 text-muted mb-3">
                <Icon name="arrow-up" className="me-2 cd-accent" />過ぎた出来事（カウントアップ）
              </h3>
              {data.past.length === 0
                ? <p className="text-muted small">過ぎた出来事はまだありません。</p>
                : data.past.map((e) => <EventCard key={e.id} event={e} />)}
            </section>
          </>
        )}
      </div>

      {deleting && (
        <DeleteDialog
          event={deleting}
          onConfirm={() => handleDelete(deleting)}
          onCancel={() => navigate('/', { replace: true })}
        />
      )}
    </>
  )
}
