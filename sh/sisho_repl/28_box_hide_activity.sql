-- 【注記】下の「戻し方」は適用前の状態を正確には再現しない: 27 は pg_prewarm の有無で分けていない・28 はロール sanoji と pg_monitor の権限を残す。戻すときは実物の権限を見てから。
-- 28_box_hide_activity.sql — 公開サーバー（sisho・rag_sisho）側:
-- 客（readonly_ai）から他の客の SQL 文を見えなくし、見張り（vitals.sh）は専用のロールに移す
-- （セキュリティ審査の指摘の残り・27 の続き）。
--
-- 背景: MCP の客は全員 readonly_ai 一本でつながる。PostgreSQL は同じロールの接続どうしに
--   pg_stat_activity の query 列を見せるので、客 A が客 B の SQL（1 行目の「目的」のコメント込み）を読めた。
--   止める関数は 27 で外した。ここでは読む口を外す。
-- 読む口: ビュー pg_stat_activity と、その下の pg_stat_get_activity(integer)・古い口の
--   pg_stat_get_backend_activity(integer)。ビューの中で呼ぶ関数も呼び手の権限で確かめられるので、
--   ビューだけ閉じても関数を直接呼べば読める＝三つとも PUBLIC から外す。
--   （pg_stat_replication などこの関数を使う他のビューも readonly_ai からは読めなくなる。MCP の道具は使っていない）
-- 見張り: 公開サーバーの vitals.sh（cron・OS ユーザー sanoji）は readonly_ai で pg_stat_activity を数えていた。
--   同名の DB ロール sanoji（LOGIN・パスワード無し）を作り pg_monitor に入れる。pg_hba の `local all all peer`
--   で Unix ソケットから OS のユーザー名のまま入れる（外からは入れない）。三つの口は pg_monitor にだけ渡す。
--   vitals.sh の接続は `-h localhost -U readonly_ai` → ソケット（-h 無し・-U 無し）に替え、MCP 客の数え方を
--   `usename=current_user` → `usename='readonly_ai'` に直す（この SQL と同じ一行の手順で流す）。
-- 開発側（VM）には流さない: 開発側の readonly_ai は開発者の調べ物に使う・外の客はいない。
--
-- 何度流しても同じ結果。流し方: sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f 28_box_hide_activity.sql
-- 戻し方: GRANT SELECT ON pg_catalog.pg_stat_activity TO PUBLIC; GRANT EXECUTE ON FUNCTION
--   pg_catalog.pg_stat_get_activity(integer), pg_catalog.pg_stat_get_backend_activity(integer) TO PUBLIC;

BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sanoji') THEN
    CREATE ROLE sanoji LOGIN;
  END IF;
END $$;
GRANT pg_monitor TO sanoji;

REVOKE SELECT ON pg_catalog.pg_stat_activity FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_catalog.pg_stat_get_activity(integer) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_catalog.pg_stat_get_backend_activity(integer) FROM PUBLIC;
GRANT SELECT ON pg_catalog.pg_stat_activity TO pg_monitor;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_stat_get_activity(integer) TO pg_monitor;
GRANT EXECUTE ON FUNCTION pg_catalog.pg_stat_get_backend_activity(integer) TO pg_monitor;

COMMIT;

-- 確かめ
SELECT has_table_privilege('readonly_ai', 'pg_catalog.pg_stat_activity', 'SELECT')                    AS roai_view,
       has_function_privilege('readonly_ai', 'pg_catalog.pg_stat_get_activity(integer)', 'EXECUTE')        AS roai_fn,
       has_function_privilege('readonly_ai', 'pg_catalog.pg_stat_get_backend_activity(integer)', 'EXECUTE') AS roai_fn_old,
       has_table_privilege('sanoji', 'pg_catalog.pg_stat_activity', 'SELECT')                          AS mon_view,
       pg_has_role('sanoji', 'pg_monitor', 'member')                                                   AS mon_member;
