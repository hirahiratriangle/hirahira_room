// どのアドレスでも EventsPage が描く。一覧・新規登録・編集の切りかえは EventsPage の中で
// アドレスを見て行う（パネルを開け閉めしても一覧を描き直さないため）。
// トーストはどこからでも出せるよう、いちばん外で包む。

import { Route, Routes } from 'react-router'

import { EventsPage } from './EventsPage'
import { ToastProvider } from './toast'

export function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path="*" element={<EventsPage />} />
      </Routes>
    </ToastProvider>
  )
}
