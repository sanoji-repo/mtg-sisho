# Sisho（司書）

[English](./README_en.md)

Magic: The Gathering のデータを取り出すライブラリアンサービス（MCP サーバー）。

カード（日本語名つき）・総合ルール・公式裁定・実デッキ統計をデータベースに揃え、
MCP（Model Context Protocol＝AI アシスタントが外部ツールを呼ぶための共通規格）のツールとして提供します。
AI のあやふやな記憶や Web の孫引きに頼らず、手元の一次データから直接引けるようにするのが役目です。

設定キーは `sisho` です。名前は MTG 公式日本語訳のカード名「Librarian（司書）」に由来します。

| 文書 | 対象・内容 |
| --- | --- |
| [docs/導入方法.md](./docs/導入方法.md) | **利用者向け**。チャットツール（Claude / ChatGPT 等）への接続手順 |
| [docs/SETUP.md](./docs/SETUP.md) | **サーバー構築者向け**。ローカルでの DB 構築やサーバー自作・運用の手順 |
| [docs/ENGINEERING.md](./docs/ENGINEERING.md) | **技術者向け**。設計思想やアーキテクチャの解説 |
| [hooks/README.md](./hooks/README.md) | **Claude Code 向け**。回答前のカード名強制検査（Stop フック） |

---

## 提供ツール一覧

クライアントから利用可能なツールは以下の通りです。

| ツール | 返すもの |
| --- | --- |
| `search_mtg_cards` | 名前（日本語/英語・部分一致）または本文のキーワードでカードを検索。並び順は名前一致優先、次に EDHREC 人気順。フォーマット指定で使用可能カードに絞り込み可能 |
| `lookup_mtg_rule` | 総合ルール（Comprehensive Rules）を条番号または英語キーワードで引く（用語集を含む）。 |
| `get_card_rulings` | カードの公式裁定（Wizards of the Coast 発行）をカード名で引く |
| `find_partner_cards` | そのカードと同じデッキに入りやすいカード（共起）を実デッキ集計から返す。フォーマット（Standard・Pioneer・Modern・Legacy・Premodern・Pauper・Vintage・Duel Commander・Commander・構築済み製品）ごとに数え、直近 90 日を優先して材料が少ないときだけ全期間へ広げる。60 枚構築ではサイドボードに何を置くかも引ける |
| `draft_pack_stats` | ドラフトのパックなど、複数のカード（1〜20 枚）の 17Lands 統計（手札に来たときの勝率・ピックの早さほか）を 1 回でまとめて引く。名前が完全に一致しないときは、そのセットのカードから近い名前を候補として返す |
| `query_mtg_database` | 読み取り専用の SQL 実行。専用ツールでカバーできない集計クエリを直接発行する（SELECT/WITH のみ・1 文・10 秒制限・最大 50 行） |
| `verify_answer` | 生成した回答文の全文を渡すと、カード名を DB と照合して「DB に存在しない名称」を検出し、正式な完成形に置換した修正版を返す |
| `mtg_probability` | デッキの確率を超幾何分布で厳密に計算（初手・t ターン目までの引き・土地の連続配置・2 枚コンボ・色マナ源）。計算は DB の SQL 関数で、返り値に式と前提を添える。言語モデルに算術をさせないための道具 |
| `find_combos` | 手持ちのカード名（デッキ 1 本まで）を Commander Spellbook の公開 API に照会し、いま組めるコンボ・あと 1 枚・色を足せば、を前提の原文と出典 URL 付きで返す。データは取り込まず都度照会（出典: Commander Spellbook） |
| `describe_mtg_tables` | テーブル一覧と列名・データ型。SQL を書く前に列名を確認するためのツール |
| `mtg_rag_health` | DB との実疎通確認・主要テーブルの行数と鮮度 |

11 ツール中 10 ツールがローカル PostgreSQL 直結（`find_combos` のみ外部 API 都度照会）で、LLM もベクトル検索も呼びません。主要ツールの応答は 1 秒未満です（ローカル実測。相方検索など重い集計や外部照会を除く）。

AI によるカード名の誤訳を防ぐため、ツールの返り値は《日本語名/英語名》の統一形式で返します。日本語版が無いカードは英語名のみ（日本語版なし）を返します。
この「LLM へのプロンプト指示よりも返り値の構造で防ぐ」という方針と、その根拠になった測定結果は [docs/bench/README.md](./docs/bench/README.md) の要約と [DESIGN.md](./DESIGN.md) に記載しています。

---

## データ

このリポジトリにデータ本体は含まれていません。
リポジトリに含まれるのは DB を構築・更新するためのスクリプト群と MCP サーバー実装です。
各テーブルの件数や構造は [DATA_MODEL.md](./DATA_MODEL.md) を、データの出所ごとの取得マナーとライセンス・著作権表示は [docs/DATA_SOURCES.md](./docs/DATA_SOURCES.md) を参照してください。

---

## 使い方

詳細な接続手順は冒頭の [docs/導入方法.md](./docs/導入方法.md) を参照してください。

---

## ライセンス

ソースコードは MIT License です。データ本体は含みません。Magic: The Gathering は Wizards of the Coast LLC の登録商標であり、本プロジェクトは非公式・非商用のファンコンテンツです。

Sisho is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

リミテッド統計の元データは 17Lands（https://www.17lands.com/）の Public Datasets（[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)）です。本プロジェクトが持つのはそこから派生した集計値であり、17Lands は本プロジェクトを承認・保証していません。

## 利用上の注意

- **回数制限（レート制限）**: 接続 URL ごと 60 回/分・サーバー全体で 300 回/分です（超過時は HTTP 429 エラーを返します）。詳細は [docs/PUBLIC_SERVER.md](./docs/PUBLIC_SERVER.md) を参照してください。
- **入力内容の記録**: 障害対応と品質改善のため、道具に送られた入力（検索語・SQL・カード名など）をサーバー側に記録します（約 5 週間で消去・第三者への提供は行いません）。
- **接続元 IP の記録**: 通常の利用時や発行ページを開いただけのときは IP を記録しません。接続 URL の発行時、不正な接続元の拒否時、および接続元ごとの過度なアクセスの遮断時に限り、濫用防止のため接続元 IP を記録します（約 5 週間で消去）。
- **免責事項**: 回答文を作成するのは利用者側の AI であり、Sisho は一次データを提供するツールです。事前のユーザー登録は不要ですが、無保証の実験的な提供となります。
- **接続 URL の管理**: 発行された URL は合言葉（秘密鍵）として扱い、他人に共有しないでください。紛失時は発行ページで再取得できます。
- **問い合わせ**: 不具合の報告・要望・質問は[問い合わせフォーム](https://docs.google.com/forms/d/e/1FAIpQLSdiAwOzxL4aCORh-vy8lnG0vOFpW-_0FWAkVK0ofviURyqybQ/viewform)（返信先は任意）または GitHub の Issues へお寄せください。
