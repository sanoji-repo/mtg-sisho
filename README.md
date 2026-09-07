# Sisho（司書）

Magic: The Gathering のデータを取り出すライブラリアンサービス（MCP サーバー）。

An MCP (Model Context Protocol) server that acts as a librarian for Magic: The Gathering data. It connects AI assistants directly to a local PostgreSQL database of cards with Japanese names, Comprehensive Rules, official rulings, and tournament deck statistics. Instead of relying on model memory or web searches, assistants retrieve verified primary data directly through standard tool calls.

カード（日本語名つき）・総合ルール・公式裁定・実デッキ統計を PostgreSQL に揃え、
MCP（Model Context Protocol＝AI アシスタントが外部ツールを呼ぶための共通規格）のツールとして提供する。
AI のあやふやな記憶や Web の孫引きに頼らず、手元の一次データから直接引けるようにするのが役目。

設定キーは `sisho`。名前は MTG 公式日本語訳のカード名「Librarian（司書）」に由来する。

| 文書 | 内容 |
| --- | --- |
| [docs/導入方法.md](./docs/導入方法.md) | 使う人向け。つないで最初の問いを投げるまで |
| [docs/ENGINEERING.md](./docs/ENGINEERING.md) | 立てる・中を見る人向け。設計や構築の技術文書 |

---

## 何ができるか

AI アシスタント（Claude Code・claude.ai のコネクタ等）にこのサーバーを接続すると、次のツールが利用できる。

| ツール | 返すもの |
| --- | --- |
| `search_mtg_cards` | 名前（日本語/英語・部分一致）または本文のキーワードでカードを検索。並び順は名前一致優先、次に EDHREC 人気順。フォーマット指定で使用可能カードに絞り込み可能 |
| `lookup_mtg_rule` | 総合ルール（Comprehensive Rules）を条番号または英語キーワードで引く。条文 3,317＋用語集 739 |
| `get_card_rulings` | カードの公式裁定（Wizards of the Coast 発行）をカード名で引く |
| `find_partner_cards` | そのカードと同じデッキに入りやすいカード（共起）を実デッキ集計から返す。統率者戦・60 枚構築・Pauper・Vintage・構築済み製品を切り替え可能 |
| `query_mtg_database` | 読み取り専用の SQL 実行。専用ツールでカバーできない集計クエリを直接発行する（SELECT/WITH のみ・1 文・10 秒制限・最大 50 行） |
| `verify_answer` | 生成した回答文の全文を渡すと、カード名を DB と照合して「DB に存在しない名称」を検出し、正式な完成形に置換した修正版を返す |
| `mtg_probability` | デッキの確率を超幾何分布で厳密に計算（初手・t ターン目までの引き・土地の連続配置・2 枚コンボ・色マナ源）。計算は DB の SQL 関数で、返り値に式と前提を添える。言語モデルに算術をさせないための道具 |
| `find_combos` | 手持ちのカード名（デッキ 1 本まで）を Commander Spellbook の公開 API に照会し、いま組めるコンボ・あと 1 枚・色を足せば、を前提の原文と出典 URL 付きで返す。データは取り込まず都度照会（出典: Commander Spellbook） |
| `describe_mtg_tables` | テーブル一覧と列名・データ型。SQL を書く前に列名を確認するためのツール |
| `mtg_rag_health` | DB との実疎通確認・主要テーブルの行数と鮮度 |

10 ツール中 9 ツールがローカル PostgreSQL 直結（`find_combos` のみ外部 API 都度照会）で、LLM もベクトル検索も呼ばない。主要ツールの応答は 1 秒未満（ローカル実測。相方検索など重い集計や外部照会を除く）。

### カード名の完成形

日本語で MTG を語るとき、AI はカード名を自分で翻訳してしまう（例: Stifle を「スティフル」、正しくは 《もみ消し/Stifle》）。
このサーバーは、カード情報を扱うツールの返り値すべてに `name_display`＝《日本語名/英語名》 という完成形の文字列を載せ、
「この文字列を一字も変えずに書く」ことを求める。日本語版が無いカードは英語名のみ（日本語版無し）で返す（Arena 専用で日本語名未収録のカードは「英語名（日本語名未収録）」）。
SQL の実行結果でも、カード名に当たる列の右隣に完成形の列を自動で添える。
最後に `verify_answer` が回答文中の名称を DB と突き合わせる。

この「LLM へのプロンプト指示よりも返り値の構造で防ぐ」という方針と、その根拠になった測定結果は [docs/bench/README.md](./docs/bench/README.md) の要約と DESIGN.md に記載している。

---

## 測定データ

名前忠実度（42 問・Claude Opus）: MCP 有りは全 effort で 42/42、MCP 無しは最良でも 22/42。
《日本語名/英語名》の書式適合（100 問 × 5 試行・Opus low）: 改変ゼロ回答 97/100（5 回目）。
測定の条件・回答原文・正直に書いておくこと・再測定の手順は [docs/bench/README.md](./docs/bench/README.md)。

---

## データ

件数の表は [DATA_MODEL.md](./DATA_MODEL.md) の頭にある。

このリポジトリにデータ本体は含まれていない。
リポジトリに含まれるのは DB を構築・更新するためのスクリプト群と MCP サーバー実装である。
データ出所ごとの取得マナーとライセンス・著作権表示は [docs/DATA_SOURCES.md](./docs/DATA_SOURCES.md) を参照。

---

## 使い方

- つないで使う（はじめての人向け）: [docs/導入方法.md](./docs/導入方法.md)
- 自分で立てる: [docs/SETUP.md](./docs/SETUP.md)（前提条件・DB 構築・定期運用・MCP サーバーの登録）
- Claude Code で回答前の強制検証: [hooks/README.md](./hooks/README.md)

### クライアント別の対応状況と機能

| 利用クライアント | 接続方式 | サーバーから伝達される情報 | 出力の強制検査（ガードレール） |
| --- | --- | --- | --- |
| claude.ai（無料プラン） | カスタムコネクタ 1 枠（公式サポートに「Free users are limited to one custom connector」と明記） | ツールの説明文と返り値のみ（`instructions` は無視される） | 不可 |
| claude.ai（Pro 以上） | カスタムコネクタ | 同上 | 不可 |
| Claude Code（Pro 以上） | stdio または HTTP 接続 | 説明文・返り値・`instructions` | 可能（[hooks/README.md](./hooks/README.md) の Stop フックを使用） |
| 独自アプリケーション（API） | 任意の実装 | 全情報 | 可能（レスポンス受信後に検証・再生成を制御） |

---

## 開発経緯・前身システムについて

本プロジェクトの前身は、「日本語の自然文で MTG カードを柔軟に探す」ハイブリッド検索システム（ベクトル検索＋全文検索＋LLM クエリルーター＋人手採点による評価基盤）だった。
しかし、2026-08 の実運用ログを分析したところ、AI アシスタントは自然言語検索ツールよりも SQL 実行ツールを圧倒的に多く利用しており（93 回の呼び出し中、SQL が 53 回・検索は 7 回）、
LLM ルーターのオーバーヘッド（6〜86 秒）は DB 直結の高速性（65 ミリ秒）に見合わないことが判明した。
クライアント側に高性能な LLM が存在する構成においては、「自然文クエリの意図解釈はクライアント側 LLM に委ね、サーバー側は検証済みの一次データを極めて高速・正確に返すことに専念する」アプローチが合理的である——
そう判断し、2026-08-21 に複雑な検索パイプラインを廃止。本リポジトリには軽量な MCP ツール群とデータ同期スクリプトのみを残す形へと再設計された。
※前身システムの評価基盤およびログ記録は別リポジトリ（非公開）に保存されている。

## ライセンス

ソースコードは MIT License。データ本体は含まない。Magic: The Gathering は Wizards of the Coast LLC の登録商標であり、本プロジェクトは非公式・非商用のファンコンテンツである。

Sisho is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.

リミテッド統計の元データは 17Lands（https://www.17lands.com/）の Public Datasets（[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)）。本プロジェクトが持つのはそこから派生した集計値であり、17Lands は本プロジェクトを承認・保証していない。

## 運用上の制限

- 公開口のレート制限: 接続元 IP ごと 60 回/分・全体 300 回/分（超過は HTTP 429 と JSON-RPC の error で理由を返す）。詳細は `docs/PUBLIC_SERVER.md`。
- 入力の記録: この接続先に送られた道具の入力（検索語・SQL・カード名など）は、障害対応と品質改善のためサーバー側に記録する。第三者には渡さない。
- 答えの責任の所在: 答えの文章を作るのは利用者側の AI である。Sisho が返すのは一次データから引いた値で、その値は記録から確認できる。
- 接続 URL は合言葉として扱う（URL は履歴やログに残る。無くしたら発行ページで取り直す）。
- 認証は無い。無保証の実験的な提供であり、問題の報告は GitHub の Issues へ。
