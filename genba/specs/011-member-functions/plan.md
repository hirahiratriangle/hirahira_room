# plan 011：メンバーの職掌

spec：[spec.md](spec.md)

モデルの変更はない。

## 集計（`kpi.py`）

`member_functions(site, user)`：本人の記録だけを読む。`Tile.status` に「済み」「これから」を持たせる。

## 画面

`/genba/kpi/`：メンバーには現場ごとの4職掌、全社の数字（参考）、質問リストの順に出す。ナビは全立場で「職掌とKPI」に揃える。
