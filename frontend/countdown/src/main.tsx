// countdown の画面の入口。
//
// Django の入れ物のページ（apps/countdown/templates/countdown/app.html）にある
// <div id="countdown-root"> に React の画面を描く。URL の頭は data-* で受け取る。

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import { configureApi } from './api'
import { App } from './App'
import './app.css'

const root = document.getElementById('countdown-root')

if (root) {
  const { basePath = '/countdown/events/', apiUrl = '', loginUrl = '' } = root.dataset
  configureApi(apiUrl, loginUrl)

  createRoot(root).render(
    <StrictMode>
      {/* basename の下のアドレス（/, /create/, /12/update/ など）で画面を切りかえる */}
      <BrowserRouter basename={basePath}>
        <App />
      </BrowserRouter>
    </StrictMode>,
  )
}
