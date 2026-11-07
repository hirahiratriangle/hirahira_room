// 読み込み中に出す、カードの形の仮置き（スケルトン）。
// 空白のまま待たせるより、どこに何が出るかが先に分かるので、待ち時間が短く感じられる。

export function Skeleton({ count = 3 }: { count?: number }) {
  return (
    <div aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="card cd-card cd-skeleton">
          <div className="card-body cd-card-body">
            <div className="cd-card-main">
              <div className="cd-bone" style={{ width: '45%', height: '1.3rem' }} />
              <div className="cd-bone" style={{ width: '30%' }} />
              <div className="cd-bone" style={{ width: '60%' }} />
            </div>
            <div className="cd-card-side">
              <div className="cd-bone cd-bone-ring" />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
