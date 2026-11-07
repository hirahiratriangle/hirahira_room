// 画面の切りかえ。アドレスは React にする前の Django の URL と同じ。
//
//   /countdown/events/               一覧
//   /countdown/events/create/        新規登録
//   /countdown/events/<id>/update/   編集
//   /countdown/events/<id>/delete/   一覧の上に削除の確認を出す
//
// どのアドレスでも Django は同じ入れ物を返す（apps/countdown/urls.py）。
// 画面の中で移るときはページを読み直さず、ここで描きかえる。

import { Navigate, Route, Routes } from 'react-router'

import { EventForm } from './EventForm'
import { EventList } from './EventList'

export function App() {
  return (
    <Routes>
      <Route index element={<EventList />} />
      <Route path="create" element={<EventForm />} />
      <Route path=":id/update" element={<EventForm />} />
      <Route path=":id/delete" element={<EventList />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
