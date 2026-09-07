# Sisho 技術文書案内（ENGINEERING.md）

自分で Sisho のサーバーを立てる人、設計原則や内部実装を確認したい技術者向けの文書一覧。
上から順に読むと全体像が把握できます。

## 読む順と各文書の内容

1. [設計原則（DESIGN.md）](../DESIGN.md)
   何で失敗して何を学んだか。道具の返り値自身で出力を制御する設計方針。

2. [自分で立てる（SETUP.md）](./SETUP.md)
   ローカル環境の構築手順。前提条件、PostgreSQL DB 構築、定期同期、MCP サーバー登録。

3. [データモデル（DATA_MODEL.md）](../DATA_MODEL.md)
   テーブル一覧と各列の型定義、生成列の計算式、インデックス構成。

4. [データの出所とライセンス（DATA_SOURCES.md）](./DATA_SOURCES.md)
   データの取得元、利用マナー、著作権表示、ライセンス条件。

5. [ベンチマーク台帳（bench/README.md）](./bench/README.md)
   モデル別の精度測定記録。カード名忠実度、書式適合率、改変測定台帳。

6. [公開サーバーの構築と運用（PUBLIC_SERVER.md）](./PUBLIC_SERVER.md)
   読み取り専用の公開サーバー運用。論理レプリケーション、セキュリティ堅牢化、レート制限。

7. [Claude Code 向け Stop フック（hooks/README.md）](../hooks/README.md)
   回答生成後の強制検証。出力を差し戻して修正続行させるクライアント側ガードレール。
