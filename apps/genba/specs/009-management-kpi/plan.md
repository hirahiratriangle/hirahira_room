# plan 009：マネジメントの職掌とKPI

spec：[spec.md](spec.md)

モデルの変更はない。

## 集計（`kpi.py`）

- `management_functions()`：4職掌の KPI とグラフ
- `management_reference()`：下に置く会社の KPI。グラフは標準売上の積み上げだけを残す
- `waiting_approvals()`：承認待ちの成長

## 画面

- `/genba/kpi/`：マネジメントには4職掌と会社の KPI（参考）を出す
- `_kpi_card.html`：職掌のカードを部品にして、会社の画面と共有する
- ナビと入口に「職掌とKPI」を出す
