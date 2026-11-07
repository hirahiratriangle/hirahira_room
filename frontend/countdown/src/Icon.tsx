// Font Awesome のアイコン（base.html が読む all.js が描く）。
//
// all.js は <i class="fas ..."> を見つけると、それを <svg> に差しかえる。
// React が描いた <i> を React の知らないうちに入れかえられると、あとで React が
// その <i> を消したり動かしたりするときに食い違う。そこで <span> で包み、
// React が触るのは <span> だけにする。中の <i> → <svg> の差しかえは React から見えない。

export function Icon({ name, className = '' }: { name: string; className?: string }) {
  return (
    <span className={className} aria-hidden="true">
      <i className={`fas fa-${name}`} />
    </span>
  )
}
