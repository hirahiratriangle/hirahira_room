// Django の API（apps/countdown/api.py）とのやりとり。
//
// 型は apps/countdown/serializers.py が返す JSON の形に合わせてある。
// どちらかを変えたら、もう一方も合わせる。

/** 予定・出来事 1件（API が返す形） */
export type CountEvent = {
  id: number
  name: string
  /** 2026-11-07 の形 */
  date: string
  memo: string
  notify: boolean
  /** 今日から見た日数差。未来なら正、過去なら負、当日は 0 */
  days_delta: number
  /** 画面に大きく出す日数（常に 0 以上） */
  count_days: number
  count_label: '残り' | '経過' | '当日'
  /** 残り日数を「○週と○日」に分けたもの */
  weeks: number
  extra_days: number
  /** 過ぎた出来事の周年（迎えていなければ 0） */
  years_passed: number
}

/** 一覧（GET /countdown/api/events/） */
export type EventList = {
  today: string
  /** これからの予定（当日を含む）。近い順 */
  upcoming: CountEvent[]
  /** 過ぎた出来事。新しい順 */
  past: CountEvent[]
}

/** 登録・編集で送る欄 */
export type EventInput = Pick<CountEvent, 'name' | 'date' | 'memo' | 'notify'>

/** 入力のまちがい。欄の名前 → メッセージの並び（DRF の形のまま） */
export type FieldErrors = Partial<Record<keyof EventInput | 'non_field_errors', string[]>>

/** API がエラーを返したとき投げる。入力のまちがい（400）なら fieldErrors に入る */
export class ApiError extends Error {
  status: number
  fieldErrors: FieldErrors

  constructor(status: number, message: string, fieldErrors: FieldErrors = {}) {
    super(message)
    this.status = status
    this.fieldErrors = fieldErrors
  }
}

// 入れ物のページ（countdown/app.html）が data-* で渡す値。main.tsx が最初に入れる
const config = { apiUrl: '', loginUrl: '' }

export function configureApi(apiUrl: string, loginUrl: string) {
  config.apiUrl = apiUrl
  config.loginUrl = loginUrl
}

/** Django が配る csrftoken の Cookie を読む（入れ物のページが ensure_csrf_cookie で配る） */
function csrfToken(): string {
  const hit = document.cookie.split('; ').find((c) => c.startsWith('csrftoken='))
  return hit ? decodeURIComponent(hit.split('=')[1]) : ''
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')
  // 書き込みには CSRF トークンが要る（Django の SessionAuthentication が確かめる）
  if (method !== 'GET') headers.set('X-CSRFToken', csrfToken())

  const res = await fetch(url, { ...init, headers, credentials: 'same-origin' })

  // ログインが切れている。ログイン画面へ移り、済んだら今の画面に戻す
  if (res.status === 401) {
    const next = encodeURIComponent(location.pathname + location.search)
    location.assign(`${config.loginUrl}?next=${next}`)
    throw new ApiError(401, 'ログインしてください。')
  }
  if (res.status === 204) return undefined as T

  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 400) {
      throw new ApiError(400, '入力を確かめてください。', data as FieldErrors)
    }
    if (res.status === 404) {
      throw new ApiError(404, '見つかりません。削除されたか、あなたの登録ではありません。')
    }
    const detail = typeof data?.detail === 'string' ? data.detail : ''
    throw new ApiError(res.status, detail || `うまくいきませんでした（${res.status}）。`)
  }
  return data as T
}

const eventUrl = (id: number) => `${config.apiUrl}${id}/`

export const api = {
  list: () => request<EventList>(config.apiUrl),
  get: (id: number) => request<CountEvent>(eventUrl(id)),
  create: (input: EventInput) =>
    request<CountEvent>(config.apiUrl, { method: 'POST', body: JSON.stringify(input) }),
  update: (id: number, input: EventInput) =>
    request<CountEvent>(eventUrl(id), { method: 'PATCH', body: JSON.stringify(input) }),
  remove: (id: number) => request<void>(eventUrl(id), { method: 'DELETE' }),
}
