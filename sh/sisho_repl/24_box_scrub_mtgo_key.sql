-- 24_box_scrub_mtgo_key.sql — 公開サーバー（sisho・rag_sisho）側:
-- MTGO 行の deck_name の末尾から、出所側の参加者キーを落とす。
--
-- 背景: deck_name は `mtgo_<イベント>_<参加者キー>` の形で、末尾は MTGO の
-- loginplayeventcourseid（無い回は loginid）。名前ではないが、MTGO の公開デッキリスト
-- ページ（プレイヤー名つき）と突き合わせれば個人に辿り着ける。DATA_MODEL.md の
-- 「公開側のデータベースには名前も ID も置かない」を実態に合わせるため、公開側でだけ
-- 末尾を落とす。開発側は重複判定の鍵として元の形のまま残す。
--
-- 順番を守る（先に UPDATE すると一意制約に当たる）。
--   1) deck_name の一意制約を外す。末尾を落とすと同じイベントのデッキが同名になるため。
--      論理レプリケーションは主キー id で当てるので、購読は壊れない。
--   2) 洗浄トリガに MTGO の分岐を足す（購読で流れてくる行に効かせる）。
--      Moxfield の NULL 化と同じ仕掛けなので、関数の名前を役目に合わせて付け替える。
--   3) 既存行を一度だけ書き換える。
--
-- 新しい公開サーバーを dump から作るときは sh/restore_public_dump.sh が同じことをする。
-- 流し方: postgres で `psql -d rag_sisho -f 24_box_scrub_mtgo_key.sql`

BEGIN;
SET LOCAL lock_timeout = '5s';   -- 取れなければ諦めて何もしない（読みを待たせない）

ALTER TABLE public.deck_list DROP CONSTRAINT IF EXISTS deck_list_deck_name_key;

CREATE OR REPLACE FUNCTION public.sisho_scrub_public() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.source = 'moxfield_edh' THEN
    NEW.source_url := NULL;
    NEW.deck_name  := NULL;
  ELSIF NEW.source LIKE 'mtgo%' AND NEW.deck_name IS NOT NULL THEN
    NEW.deck_name  := regexp_replace(NEW.deck_name, '_[0-9]+$', '');
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS sisho_scrub_moxfield ON public.deck_list;
DROP TRIGGER IF EXISTS sisho_scrub_public   ON public.deck_list;
CREATE TRIGGER sisho_scrub_public BEFORE INSERT OR UPDATE ON public.deck_list
  FOR EACH ROW EXECUTE FUNCTION public.sisho_scrub_public();
ALTER TABLE public.deck_list ENABLE ALWAYS TRIGGER sisho_scrub_public;

DROP FUNCTION IF EXISTS public.sisho_scrub_moxfield();

UPDATE public.deck_list
   SET deck_name = regexp_replace(deck_name, '_[0-9]+$', '')
 WHERE source LIKE 'mtgo%' AND deck_name ~ '_[0-9]+$';

COMMIT;

-- 確認（どちらも 0 になること）
--   SELECT count(*) FROM public.deck_list WHERE source LIKE 'mtgo%' AND deck_name ~ '_[0-9]+$';
--   SELECT count(*) FROM public.deck_list WHERE source = 'moxfield_edh' AND deck_name IS NOT NULL;
