# tasks 001：メンバー支援kit

plan：[plan.md](plan.md)

- [x] T1 元資料の docx から `genba/kit_data.py` を生成する（12 Issue・44項目、危険信号7、言い回し8、ワード確認表30語。顧客名を除く）
- [x] T2 `genba/kit.py`：docs 8区分、Issue の種類とラベル、現場の一生、チェックリストの組み立てと作成
- [x] T3 モデルを組み直し、`0001_initial` を作り直す（Site・Membership・Document・Term・Question・ChecklistItem・Summary・PromiseCheck）
- [x] T4 `genba/signals.py`：危険信号の判定
- [x] T5 フォーム：現場、Issue（4種）、作業の予定・実績、文書（週報4欄）、用語（意味は任意）、まとめ
- [x] T6 ビュー・URL：テンプレート、言い回し集、現場の作成・Archive、Issue の作成・詳細・チェック項目、まとめ、進捗表
- [x] T7 テンプレート：現場のページ（タブ組み直し・危険信号）、Issue の作成・詳細、まとめ、テンプレート、言い回し集、メンバーのホーム
- [x] T8 既存画面の追従：リーダー（詰まり）、マネジメント・会社（種類の名前）、約束の文言
- [x] T9 `seed_genba` を新しいモデルとデモの筋書きに合わせる
- [x] T10 テストを直し・足す。`manage.py test genba` と全体のテストを通す
- [x] T11 デモデータで全画面のスクリーンショットを撮り、spec の受け入れ条件を確かめる
- [x] T12 開発プロセス・spec の状態を更新する
