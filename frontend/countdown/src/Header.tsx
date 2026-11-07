// 画面の上の青い見出し。テンプレートのころの <header> と同じ見た目。

import { useEffect, type ReactNode } from 'react'

type Props = {
  title: string
  /** ブラウザのタブに出す名前の頭（「一覧」「新規登録」など） */
  tabTitle: string
  children?: ReactNode
}

export function Header({ title, tabTitle, children }: Props) {
  useEffect(() => {
    document.title = `${tabTitle} - カウントアップ＆ダウン | HirahiraRoom`
  }, [tabTitle])

  return (
    <header className="cd-header">
      <div className="container px-5 py-4">
        <div className="text-center text-white">
          <h1 className={`h3 fw-bold ${children ? 'mb-2' : 'mb-0'}`}>{title}</h1>
          {children}
        </div>
      </div>
    </header>
  )
}
