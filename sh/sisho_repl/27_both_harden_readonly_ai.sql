-- 【注記】下の「戻し方」は適用前の状態を正確には再現しない: 27 は pg_prewarm の有無で分けていない・28 はロール sanoji と pg_monitor の権限を残す。戻すときは実物の権限を見てから。
-- 27_both_harden_readonly_ai.sql — 開発側（VM・rag_dev）と公開サーバー（sisho・rag_sisho）の両方:
-- 客の SQL が通る readonly_ai を「本当に読むだけ」に絞る。
--
-- 背景: セキュリティ審査の指摘（他の客への干渉・一時表の作成・拡張の関数の実行）。
--   H1: MCP の客は全員 readonly_ai 一本でつながる。PostgreSQL は同じロールの接続どうしに
--       pg_cancel_backend・pg_terminate_backend を許す（PUBLIC 既定）＝客 A が客 B の処理を止め・切断できた
--       （VM で再現）。関数の実行権を PUBLIC から外す。超ユーザー（VM の devuser・公開サーバーの postgres）は影響を受けない。
--       他の客の SQL が pg_stat_activity で見える方は、この SQL では触らない（別に調べる）。
--   H3: readonly_ai は TEMP 権限（PUBLIC 既定）で `SELECT … INTO TEMP` の一時表を作れた（VM で再現）。
--       temp_file_limit は明示の一時表を数えない。DB の TEMP を PUBLIC から外す。
--       一時表を使う夜間の脚本（diff_apply.py など）は超ユーザーで動くので影響しない。
--   M1: 入口の「SELECT/WITH だけ」は読み取り専用の保証ではない。readonly_ai の取引を既定で読み取り専用にする
--       （書き込む CTE などは DB が拒む）。pg_prewarm（任意の表を共有バッファに載せる）の実行権も外す。
--       autoprewarm の裏の働き手は関数の実行権に依らない。
-- 残すもの: set_config と pg_settings の UPDATE（その接続の中だけの設定・MCP は呼び出しごとに接続を作る）。
--
-- 何度流しても同じ結果（REVOKE と ALTER ROLE … SET は冪等）。
-- 流し方: 超ユーザーで対象の DB に `psql -d <DB> -f 27_both_harden_readonly_ai.sql`
--   VM: docker exec -i pg18-primary sh -c 'psql -U "$POSTGRES_USER" -d rag_dev' < 27_both_harden_readonly_ai.sql
--   公開サーバー: sudo -u postgres psql -d rag_sisho -f 27_both_harden_readonly_ai.sql
-- 戻し方（要るとき）: GRANT EXECUTE ON FUNCTION pg_catalog.pg_cancel_backend(integer),
--   pg_catalog.pg_terminate_backend(integer, bigint), public.pg_prewarm(regclass, text, text, bigint, bigint) TO PUBLIC;
--   GRANT TEMP ON DATABASE <DB> TO PUBLIC; ALTER ROLE readonly_ai RESET default_transaction_read_only;

BEGIN;
SET LOCAL lock_timeout = '5s';

REVOKE EXECUTE ON FUNCTION pg_catalog.pg_cancel_backend(integer) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_catalog.pg_terminate_backend(integer, bigint) FROM PUBLIC;

DO $$
BEGIN
  IF to_regprocedure('pg_prewarm(regclass, text, text, bigint, bigint)') IS NOT NULL THEN
    EXECUTE 'REVOKE EXECUTE ON FUNCTION pg_prewarm(regclass, text, text, bigint, bigint) FROM PUBLIC';
  END IF;
  EXECUTE format('REVOKE TEMP ON DATABASE %I FROM PUBLIC', current_database());
END $$;

ALTER ROLE readonly_ai SET default_transaction_read_only = on;

COMMIT;

-- 確かめ（readonly_ai から見た値・新しい接続で効く）
SELECT has_function_privilege('readonly_ai', 'pg_catalog.pg_cancel_backend(integer)', 'EXECUTE')          AS cancel,
       has_function_privilege('readonly_ai', 'pg_catalog.pg_terminate_backend(integer, bigint)', 'EXECUTE') AS terminate,
       has_database_privilege('readonly_ai', current_database(), 'TEMP')                                   AS temp,
       (SELECT array_to_string(setconfig, ' | ') FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
         WHERE r.rolname = 'readonly_ai' AND s.setdatabase = 0)                                             AS role_settings;
