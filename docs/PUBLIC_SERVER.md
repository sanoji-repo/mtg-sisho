# 公開サーバーの組み立て（PUBLIC_SERVER.md）

> 旧名「売り場（SALES_FLOOR.md）」・2026-09-07 に改名。内部の呼び名で、金銭の授受があるように読めるため。古い作業記録に出る「売り場」はこの文書のこと。

Sisho の公開サーバー（読み取り専用の複製）を、家の PC とは別の機械に建てる手順。
開発側（データを作る側）は今までの VM のまま。公開サーバーは dump から数分で作り直せる使い捨ての機械として扱う。
2026-08-23 に VM 内で予行済み（dump 66MB・復元 53 秒）。実機では未検証。

## 役割

| 機械 | 役割 | 中身 |
| --- | --- | --- |
| 開発側（家の VM） | データを作る | 夜間ジョブ・搬入ジョブ・共起の洗い替え・ベンチ。`sh/make_public_dump.sh` で公開サーバー用 dump を書き出す |
| 公開サーバー（別の機械） | 返事をする | PostgreSQL（公開サーバー用の表だけ・読み取り専用）・MCP サーバー・Tailscale。`sh/restore_public_dump.sh` で dump を受ける |

公開サーバーに置かないもの: プレイヤー名（開発側の `players` 表に隔離・dump にも publication にも含めない・`deck_list.player_name` 列は 2026-08-31 に廃止）・バックアップ表・埋め込み表・評価の表・呼び出しログ。

## 機械を選ぶときの確認（中古を買う前に）

1. 世代: x86 なら Core i 第 4〜8 世代あたり（64bit・待機 10〜25W）。シンクライアント（FUTRO・HP t 系）はファンレスで 5〜10W。Raspberry Pi 5 なら 8GB（4GB で足りるかは未確認）
2. メモリ: 規格（DDR3/DDR3L/DDR4・SO-DIMM/DIMM）と最大容量。公開サーバーの DB は約 0.9GB なので 4GB あれば足りる。RAM に DB を展開するなら 8GB
3. 記憶装置: OS 用に何でもよい（HDD でも可）。DB を SD カードに置くのは避ける（RAM 展開か SSD）
4. 電源: AC アダプタが付属するか（シンクライアントは別売りが多い）。BIOS に「AC 復帰で自動起動」があるか
5. 保証: 動作確認済み・保証付きの棚から。店頭で BIOS まで上げられるなら上げる。USB のライブ Linux を持って行けば NIC まで見られる

## 組み立て（Ubuntu Server 24.04 / 26.04 で確認・Debian でも同じ）

26.04（resolute）は PGDG の公式スクリプトが対応済み（2026-08-29 実測）。Python 3.14 でも requirements.txt はそのまま入る（AVX 無しの CPU でも可・numpy 系は使っていない）。apt/pip は HDD＋Wi-Fi で合計 15 分ほど。

### 1. OS と土台

```bash
sudo apt update && sudo apt install -y git python3-venv python3-pip curl ca-certificates
# PostgreSQL 18（PGDG）。Docker を使うなら代わりに docker を入れて postgres:18 を立てる
sudo apt install -y postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt install -y postgresql-18 postgresql-contrib
```

有線 NIC はインストーラが Wi-Fi だけを書いた場合 netplan に項目が無く、ケーブルを挿してもリンク検知止まりで IP が付かない。`/etc/netplan/10-wired.yaml` に `ethernets: {enp3s0: {dhcp4: true, dhcp4-overrides: {route-metric: 100}}}` を足して `netplan apply`（Wi-Fi は metric 600 で予備に残る・2026-08-29 実測）。

PostgreSQL は `127.0.0.1` だけで待ち受ける（既定のまま）。外から DB には触らせない。MCP が同じ機械から繋ぐだけ。

### 2. Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # 家の VM と同じ tailnet に入れる
```

Tailscale の管理画面で、この機械を tag 付きノードにし（鍵の期限が切れない）、ACL で「VM → 公開サーバーの ssh と 8765」だけ許し「公開サーバー → 他」は拒否する。公開サーバーが踏み台にならないため。

### 3. リポジトリと venv

```bash
git clone <mtg-sisho の URL> ~/mtg-sisho && cd ~/mtg-sisho
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp env.example .env && chmod 600 .env     # DB_* を公開サーバーの値に・DB_PASS_ROAI を決める・MCP_HTTP_PATH を秘密の値に
```

### 4. dump を受けて復元する

開発側で `sh/make_public_dump.sh` を走らせ、できた `sisho_public_YYYYMMDD.dump` と `.sha256` を Tailscale 経由で公開サーバーへ送る（`scp` か `tailscale file cp`）。公開サーバーで:

```bash
# dump とスクリプトは postgres ユーザーが読める場所（/tmp）へ置く。sudo -E は新しい sudo（Ubuntu 26.04 の sudo-rs）で無視されるので env で渡す。
sudo cp ~/sisho_public_YYYYMMDD.dump ~/sisho_public_YYYYMMDD.dump.sha256 sh/restore_public_dump.sh /tmp/ && sudo chmod 644 /tmp/sisho_public_*
sudo -u postgres env DB_PASS_ROAI=<.env と同じ値> PGUSER=postgres PGHOST=/var/run/postgresql \
  bash /tmp/restore_public_dump.sh /tmp/sisho_public_YYYYMMDD.dump rag_sisho
```

実測（2026-08-29・Lenovo G50-30・N2830 2 コア・HDD・Ubuntu 26.04）: 復元 104 秒・全体 131 秒（dump 68MB）。scp は Tailscale 越しで 16 秒。

やること: sha256 検証 → `rag_sisho_new` に復元 → `player_name` を落とす → `readonly_ai` を作って SELECT を許可・資源上限（接続 6・statement_timeout 10 秒・work_mem 16MB・temp_file_limit 256MB）・`pg_sleep` 系の実行権を外す → ANALYZE → 煙試験（行数・《もみ消し/Stifle》・曖昧名）→ `rag_sisho` と差し替え（旧は `rag_sisho_old` に残る）。
どの段で失敗しても、今動いている `rag_sisho` には触らない。

2 回目以降も同じコマンド。差し替えの瞬間に繋いでいたセッションは切れるが、MCP は呼び出しごとに繋ぎ直すので次の呼び出しから新しい DB に着く。

### 4b. 中身を自動で追従させる（論理レプリケーション・2026-08-30 から本線）

§4 の dump→復元は「公開サーバーの初期化・作り直し」に使い、日々の中身は開発側（家の VM）から論理レプリケーションで追従させる。
向きは 開発側＝publisher・公開サーバー＝subscriber（公開サーバーが開発側の PG に繋ぎに行く）。公開サーバーの「外へ出ない」姿勢に、この 1 口だけ針の穴を開ける。

開発側（VM）:
1. PostgreSQL を `wal_level=logical` にして再起動（docker compose なら command に `-c wal_level=logical`）。`max_slot_wal_keep_size` も置く（公開サーバーが長く落ちても開発側のディスクが埋まらない保険）。
2. 公開サーバーから届く口を開ける（docker の ports に Tailscale の IP・Tailscale ACL に `tag:sisho → <VM>:5435`）。pg_hba は `host all all all scram-sha-256` があれば足りる。
3. `sh/sisho_repl/01_vm_publication.sql` を流す（ロール `sisho_repl`＝REPLICATION＋公開する列だけ SELECT／publication `sisho_pub`＝19 表（`limited_card_stats`・17Lands 追加集計 5 表 `limited_*`・`mtg_sets` を含む・`make_public_dump.sh` の TABLES と同じ）・全表を明示の列指定——ただし主キーの無い `mtg_cards_v2_nonlegal`（REPLICA IDENTITY FULL）だけは列指定なし。列指定を付けると UPDATE/DELETE が「Column list used by the publication does not cover the replica identity」で拒否される（2026-08-31 実測））。
   - `deck_list` の列指定に `player_name` を入れない＝名前は公開サーバーへ流れない。
   - 生成列（`mtg_cards_v2.name_display`）は列指定に入れない（公開サーバーが自分で計算する。入れると公開サーバー側で "incompatible generated column"）。
   - 主キーの無い表（`mtg_cards_v2_nonlegal`）は `REPLICA IDENTITY FULL`。

公開サーバー側:
1. `~postgres/.pgpass` に `<VM>:5435:rag_dev:sisho_repl:<パスワード>`（接続文字列に秘密を埋めない）。
2. `sh/sisho_repl/02_box_subscription.sql` を postgres で流す＝ `deck_list.deck_name` の NOT NULL を外す → Moxfield 行の `source_url`/`deck_name` を NULL にするトリガ（ENABLE ALWAYS——購読側の apply は既定のトリガを鳴らさない）→ 12 表を空に → `CREATE SUBSCRIPTION sisho_sub`。初回コピーは約 0.9GB（回転 HDD で 30 分）。
3. 稼働中の公開サーバーに表を後から足すとき（2026-08-31 の `limited_card_stats` が実例）は `sh/sisho_repl/04_box_add_limited_card_stats.sql`（2026-09-02 の追加集計 5 表は `15_vm_add_limited_extra.sql`／`16_box_add_limited_extra.sql`・2026-09-03 の `mtg_sets` は `17_vm_add_mtg_sets.sql`／`18_box_add_mtg_sets.sql`）を postgres で流す＝表を作って `readonly_ai` に SELECT → `ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION`（3MB・数秒）。新しく作る公開サーバーは §4 の dump に表ごと入っているので不要。`/tmp` に置いた SQL は `chmod 644`（postgres が読めるように・600 のままだと Permission denied）。
4. 確認: 公開サーバー `SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1`（13 行・全部 r）／開発側 `SELECT slot_name, active, wal_status FROM pg_replication_slots`（active・reserved）。

運用:
- 開発側で公開表に列を足したら「公開サーバーに ADD COLUMN → 開発側で publication から表を外して列指定を足して入れ直す（`ALTER PUBLICATION … DROP TABLE` → `ADD TABLE …(列)`・列指定は差し替えられない）→ 01 にも反映 → 公開サーバーで `ALTER SUBSCRIPTION sisho_sub REFRESH PUBLICATION WITH (copy_data = false)`」（copy_data=false＝中身のある表を COPY し直して主キー衝突させない）。実例は `05_vm_digital_column.sql`／`06_box_digital_column.sql`（2026-08-31・`digital` 列）。外している間の書き込みは公開サーバーへ流れないので、その間は表を書かない。
- 生成列（`mtg_cards_v2` の `card_name`・`japanese_name`・`name_display`）は publication に載せられない。公開サーバーにも同じ式で作り、公開サーバー自身が計算する（2026-08-31・`sh/sisho_repl/09a〜12`）。列指定に新しい列を足した後、既存行の値は REFRESH では流れない → 開発側で `UPDATE 表 SET 列 = 列` と全行に触って流す。
- 表を足すときは「開発側で GRANT＋`ALTER PUBLICATION sisho_pub ADD TABLE …（列指定）` → 公開サーバーに同じ列の表を作って `readonly_ai` に GRANT → 公開サーバーで REFRESH PUBLICATION」（`03_vm_add_limited_card_stats.sql`／`04_box_add_limited_card_stats.sql` が実例・2026-08-31）。01 と `make_public_dump.sh` の TABLES にも同じ表を足しておく（01 は作り直しの正本・dump は新しい公開サーバーの初期化）。表は public に置く（別スキーマだと既定 ACL・dump・名前解決の例外が増える＝8/31 に `lab17.card_stats` を public へ統合した理由）。
- 公開サーバーが長く落ちて開発側のスロットが `lost` になったら、公開サーバーで `DROP SUBSCRIPTION sisho_sub` → 02 をやり直す。
- §4 の復元を走らせるときは先に `DROP SUBSCRIPTION sisho_sub`（`restore_public_dump.sh` が検査して止まる）。

### 5. MCP を常駐させる

推奨（2026-08-30・公開サーバーで採用）: 専用ユーザーで動かす。MCP に穴があっても sudo 持ちのユーザーに届かないようにする。

```bash
sudo useradd -r -m -s /usr/sbin/nologin mcp                 # sudo 無し・ログイン不可
sudo mkdir -p /opt/mtg-sisho /var/log/mtg-sisho
sudo chown sanoji:mcp /opt/mtg-sisho && sudo chmod 750 /opt/mtg-sisho   # コードは運用者が所有・mcp は読むだけ
sudo chown mcp:mcp /var/log/mtg-sisho && sudo chmod 750 /var/log/mtg-sisho  # mcp が書けるのはここだけ
rsync -a --exclude .venv --exclude .env ~/mtg-sisho/ /opt/mtg-sisho/ && cp ~/mtg-sisho/.env /opt/mtg-sisho/
cd /opt/mtg-sisho && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo chown -R sanoji:mcp /opt/mtg-sisho && sudo chmod -R g+rX,g-w,o-rwx /opt/mtg-sisho && sudo chmod 640 .env
# .env の MCP_TOOL_LOG を /var/log/mtg-sisho/mcp_tools.log にする
```

システム unit `/etc/systemd/system/mtg-rag-mcp.service` に `User=mcp`・`WorkingDirectory=/opt/mtg-sisho`・`EnvironmentFile=/opt/mtg-sisho/.env`・`ExecStart=/opt/mtg-sisho/.venv/bin/python /opt/mtg-sisho/src/mcp_server.py http 8765` を書き、砂場として `NoNewPrivileges=yes`・`ProtectSystem=strict`・`ProtectHome=yes`・`ReadWritePaths=/var/log/mtg-sisho`・`PrivateTmp=yes` を付ける。`sudo systemctl enable --now mtg-rag-mcp`。ログの取り回しは `/etc/logrotate.d/mtg-sisho`（週 1・52 世代）。

以下は旧手順（運用者のユーザー unit で動かす形・sudo 持ちのユーザーで動くので上の形を推奨）。

`deploy/mtg-rag-mcp.service` を `~/.config/systemd/user/` に置き、`WorkingDirectory`・`ExecStart` のパスを公開サーバーのものに直す（`~/mtg-sisho`・`.venv/bin/python`）。`EnvironmentFile` で `.env` を読ませる。 venv に mcp が入るので `Environment=PYTHONPATH=...` の行は消す（作者環境の都合）。`ExecStartPre` の待ち受けポートは公開サーバーの PostgreSQL（5432）に直す。`.env` の `DB_USER` は `readonly_ai`・`DB_PASSWORD` は `DB_PASS_ROAI` と同じ値でよい（MCP は書き込みをしないので全道具を読み取り専用ロールで動かす）。`DB_FLAG_FILE` の既定はリポジトリ直下の `.primary_updating`（2026-09-05 以降・以前は作者環境の絶対パス）。公開サーバーでは更新処理を走らせないので、存在しないパスに上書きしておくと確実。

```bash
sudo loginctl enable-linger $USER
systemctl --user daemon-reload && systemctl --user enable --now mtg-rag-mcp
curl -s http://127.0.0.1:8765$MCP_HTTP_PATH   # 何か返れば生きている
```

### 6. 公開口を切り替える

```bash
sudo tailscale funnel --bg 8765      # この機械の Funnel で 8765 を公開
```

VM 側の Funnel を止め、claude.ai のコネクタの URL を公開サーバーのホスト名に貼り替える。ホスト名は証明書の透明性ログで公開されるので、待ち受けパス（`MCP_HTTP_PATH`）を秘密の値にしておく。

### 7. 見張りと復旧

- 開発側の VM から 5〜15 分ごとに Funnel の URL で `mtg_rag_health` を叩く（返事が無ければ通知）
- 公開サーバーが壊れたら: OS を入れ直し → 1〜6 をやり直す。データは dump から戻る（開発側に正本がある）
- 停電対策が要るなら UPS（Pi なら UPS HAT か大きめのモバイルバッテリー）。ルーターも一緒に生かさないと外から届かない

## 守り（2026-08-30・公開サーバーで実施した順）

前提: Funnel は家のルーターにも tailnet にも入口を開けない（入ってくるのは公開サーバーの 127.0.0.1:8765 への HTTP だけ）。守るのは「MCP に穴があったとき、機械の外へ広がらないこと」。

1. MCP は専用ユーザー（§5・sudo 無し・コード読み取り専用・書けるのはログ置き場だけ・systemd の砂場）
2. Tailscale ACL: 公開サーバー → 他ノードは拒否（§2）
3. ufw は外向きも既定拒否: 許すのは ルーター宛 DNS/DHCP・Tailscale（41641/3478 udp・tailscale0）・80/443 tcp（Tailscale 制御・DERP・Discord・apt・pip）・NTP（123 udp・4460 tcp）。家の LAN 宛はルーター以外拒否（Tailscale 直結用の 41641/udp だけ例外）。これで乗っ取られても LAN の他の機械に届かず、踏み台としても 80/443 宛しか出られない。

```bash
sudo ufw default deny outgoing
sudo ufw allow out to <ルーター> port 53 proto udp; sudo ufw allow out to <ルーター> port 53 proto tcp; sudo ufw allow out to <ルーター> port 67 proto udp
sudo ufw allow out to <LAN>/24 port 41641 proto udp
sudo ufw deny  out to <LAN>/24
sudo ufw allow out on tailscale0; sudo ufw allow out 41641/udp; sudo ufw allow out 3478/udp
sudo ufw allow out 443/tcp; sudo ufw allow out 80/tcp; sudo ufw allow out 123/udp; sudo ufw allow out 4460/tcp
sudo ufw reload
```
適用の前に LAN 直の ssh（inbound 22 from LAN）を控えに残しておく。適用後に Tailscale の直結（`tailscale ping`）・DNS・Discord・apt が通ること、LAN の他の機械と外の 22/25 が拒否されることを実測する。

4. Lynis で答え合わせ: `sudo apt install lynis && sudo lynis audit system` → 警告は全部潰す・提案は「この機械に意味があるか」で選ぶ（企業向け項目=外部ログホスト・auditd・GRUB パスワード等は見送ってよい）。unit の砂場は `systemd-analyze security mtg-rag-mcp` の点数で確認（公開サーバーの実測: 7.8 → 1.3・Hardening index 64 → 72・2026-08-30）。

残る性質の違う穴: 秘密パスを知られたときの DoS（レート制限は未実装）・pip の供給元・物理。

## DB を RAM に置く形（任意）

公開サーバーの DB は約 0.9GB なので、8GB の機械なら tmpfs に置いて毎回 dump から作り直す形もとれる（記憶装置に一切書かない）。
起動時に `pg_restore` が数分かかり、その間は返事ができない。MCP の unit を復元完了の後に起動するよう縛る。実機で復元時間を測ってから採るかどうか決める。

## 未検証（正直に）

- 実機（Pi 5・シンクライアント）での復元時間と応答速度
- Funnel が CGNAT（モバイル回線など）の下で動くこと（仕組み上は動くはず）
- Raspberry Pi OS に PGDG の postgresql-18 が無改造で入ること

## 規模の見積もりと台数を増やす形（2026-09-07）

### 今の公開サーバーで持つ範囲

- 機体: Lenovo G50-30・Celeron N2830（2 コア・2014 年・AVX 無し）・RAM 3.3GB・HDD 1 本。DB 本体は約 0.9GB で RAM のキャッシュに全部収まる。
- 実測（2026-08-30〜09-07・道具ログ 8 日・1,594 回）: 1 分あたり最大 17 回・同時実行の最大 1・DB 時間の中央値 0.30 秒。
- 実測の天井（2026-09-07・VM から tailnet 越しに 20 秒ずつ・閉ループ）: 軽い呼び出し（名前検索）は同時 4 で毎秒 11 本・CPU 89%、同時 8 でも毎秒 11 本のまま（CPU 97%・p50 0.68 秒）＝毎分およそ 670 回が天井。今の客足の 40 倍・入口のレート制限（全体 300 回/分）の 2.2 倍なので、先に上限になるのはレート制限。1 本あたりの CPU はおよそ 0.29 コア秒で、DB より Python と TLS の取り分が大きい（内訳は未測定）。
- 重い呼び出しが 4 本同時（相方検索の構築 scope・1 本 5 秒）に走って CPU が 97% でも、軽い呼び出しは p90 0.53 秒で返り続けた（下の「DB のスロット」の予約スロットが効く・エラー 0）。
- 先に詰まる所は CPU でなく (1) 重い集計（相方検索の構築 scope: 1〜7 秒） (2) キャッシュに無い読み（HDD）。手を打つ順: 重い集計を夜間ジョブで表にする → HDD を SSD に → 台数を増やす。
- DB のスロット（2026-09-07）: 同時実行は 5 スロット。重いレーン（自由 SQL・相方検索）は 4 スロットまで・軽いレーンは statement_timeout 1 秒で、切られたらスロットを返して重いレーン（10 秒）に並び直す。順番待ちは 20 秒で「混雑」を返す。重い問い合わせが 4 本並んでも、軽い問い合わせのスロットが 1 つ必ず残る。
- 開発側（VM）が止まっている間も、公開サーバーは最後に受け取った状態で答え続ける。複製は開発側が戻れば追いつく（2026-09-06 に開発側が 8 時間止まって確認・データの欠けは無し）。

### 台数を増やす形

公開サーバーは開発側からの論理レプリケーションの購読者（読み取り専用の複製）で、MCP は stateless（どの公開サーバーがどの呼び出しを受けても同じ答え）。だから 2 台目は「同じ手順でもう 1 台組んで、同じ publication を購読させる」だけで作れる。足りないのは客をどの台に渡すか決める玄関（ロードバランサ＝振り分け役）。

玄関の候補（2026-09-07 に公式文書で確認）:

| 候補 | 振り分けできるか | 理由 |
| --- | --- | --- |
| 家のルーター | できない | NAT のポート転送は 1 ポート 1 台。しかも Funnel は公開サーバーから外へ張る接続で、ルーターは経路に居ない |
| Tailscale Funnel | できない | Funnel の名前はノードごと（`機体名.tailnet.ts.net`）で、同じ名前を 2 台で受ける機能は無い |
| 自前のリバースプロキシ（手前で受けて後ろの台へ渡す代理） | できる | 1 台目の nginx か Caddy に Funnel を向け、そこから自分と 2 台目へ振る。公開 URL は変わらない。1 台目が単一障害点になるが仕事は軽い |
| Cloudflare Tunnel の複製（同じトンネルを複数の台で張る） | できる | 公式機能。1 トンネルにつき最大 25 台。振り分けの割合は制御できない（要るならトンネルを分けて Cloudflare の Load Balancer を使う）。公開 URL が自分のドメインに変わる＝配布前に決める話 |

- 引き金（構想・未実施）: 毎分 100 回超か、応答の p90 が 2 秒超の状態が 1 週間続いたら、上の順（表にする → SSD → 台数）で手を打つ。それまでは増やさない。
- 参照: [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel)・[Cloudflare Tunnel の複製](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-availability/deploy-replicas/)・[Cloudflare の公開ロードバランサ](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/public-load-balancers/)

## レート制限（2026-09-02）

公開 URL を配る前の門。`src/mcp_server.py` の `_RateLimiter`（滑走窓 60 秒）と `_RateLimitASGI`（ASGI の外皮）で、uvicorn が `X-Forwarded-For` を client に解決した後の IP で数える（Funnel → `127.0.0.1:8765` 直結・前段の代理は無い）。

- 既定: 接続元 IP ごと 60 回/分・全体 300 回/分。`.env` の `MCP_RATE_PER_IP_MIN`／`MCP_RATE_GLOBAL_MIN` で変更（0 で無効）。`MCP_RATE_EXEMPT`（既定 `127.0.0.0/8,100.64.0.0/10`）は数えない接続元（tailnet からの監視）。
- 超過は HTTP 429・`Retry-After` ヘッダ・本文は JSON-RPC の error（日本語の理由つき＝呼び出し側の LLM に届く）。拒否した呼び出しは窓に数えない（`Retry-After` の値を正直に保つ）。
- 記録: journal に `[rate] 429 ip=… reason=…` を IP ごと 1 分 1 行（洪水でログを埋めない）。
- 前提: claude.ai の呼び出しは Anthropic の出口 IP 群（一つの会話でも 30 個ほどを回る）から来る。IP ごとの枠は「一人当たり」ではなく単一の直叩きを抑える粗い門で、公開サーバーを守る本命は全体の枠。同時実行は DB のスロット取り（5）が別に抑える。平常時の実測は IP ごと最大 8 回/分・全体 22 回/分。
- 試験: `tests/test_ratelimit.py`（時計を差し替えた純粋な試験・DB に触らない）。実弾は Funnel の公開入口経由で 66 連発 → 60 通過・61 回目から 429 を確認（2026-09-02）。

## 確率計算の SQL 関数（2026-09-04）

`sql/prob_functions.sql` を開発側と公開サーバーの両方で流す（論理レプリケーションは関数を運ばない・`CREATE OR REPLACE` で冪等）。公開サーバーは `scp` → `/tmp` → `chmod 644` → `sudo -u postgres psql -d rag_sisho -v ON_ERROR_STOP=1 -f /tmp/prob_functions.sql`。`readonly_ai` は既定の PUBLIC EXECUTE で呼べる。新しい公開サーバーを作るときは dump の後にこのファイルを流す（dump は表だけ）。

## 索引（2026-09-04）

索引は論理レプリケーションで運ばれないので、開発側と公開サーバーの両方で作る（`sh/sisho_repl/21_both_add_indexes.sql`・`CREATE INDEX CONCURRENTLY`＝適用と道具を止めない）。公開サーバーの統計（`pg_stat_user_tables` の順次走査・`pg_stat_user_indexes` の使用回数）で足りない索引と使われない索引を定期的に見る。膨張は `pgstattuple` の `pgstatindex()` で葉の密度を測る（新品は約 90%・40% 未満は作り直し）。公開サーバーの `readonly_ai` は `max_parallel_workers_per_gather = 0`（2 コアでスロット 5 の取り合いを避ける）。

## 門と札（2026-09-07）

接続 URL を一人ひとりに「札」（`secrets.token_urlsafe(24)`・32 字）として配り、札ごとにレート制限を課す仕組み（小片 8）。
身元は聞かず名簿も持たない。発行ページ（`/issue`）のボタン一つで配る。

### 環境変数

| 変数名 | 既定値 | 説明 |
| --- | --- | --- |
| `MCP_FUDA_FILE` | `<repo>/state/fuda.tsv` | 札の台帳 TSV（公開サーバーでは `/var/log/mtg-sisho/fuda.tsv`） |
| `MCP_FUDA_PREFIX` | `/mcp/` | 札付き接続 URL のパス接頭辞（`/mcp/<札>`） |
| `MCP_FUDA_PER_MIN` | `60` | 札ごとの 1 分間あたりの呼び出し枠 |
| `MCP_ISSUE_PER_DAY` | `3` | 発行元 IP ごとの 1 日あたりの最大発行数 |
| `MCP_ISSUE_GLOBAL_DAY` | `100` | サーバー全体の 1 日あたりの最大発行総数（IP ごとの枠と同じく `fuda.tsv` の issue 行で数える＝再起動で消えない） |
| `MCP_LEGACY_PATH` | `1` | 旧パス（`MCP_HTTP_PATH` の値・秘密の長いパス）の有効化フラグ（`0` で旧パスを閉じる） |
| `MCP_PUBLIC_BASE` | 空 | 空なら `Host` ヘッダだけから組む（`X-Forwarded-Host` は見ない）。公開サーバーでは必ず明示 |
| `MCP_ISSUE_PATH` | `/issue` | 接続 URL 発行ページのパス |
| `MCP_COMBOS_PER_MIN` | `10` | 外部 API（Commander Spellbook）を守るための札ごとの 1 分間あたりの枠 |
| `MCP_COMBOS_GLOBAL_MIN` | `60` | Commander Spellbook 照会の全体の 1 分間あたりの枠 |

### fuda.tsv の置き場と行の形

台帳は SQLite や DB ではなく追記専用の TSV ファイル（1 行 1 事象・最新行が真）。
公開サーバーでは ProtectSystem=strict のため書ける場所が `/var/log/mtg-sisho` に限られ、logrotate（`*.log` を週 1 で copytruncate）の対象外とするため拡張子は `.tsv` とする。

各行の構成（タブ区切り 6 列）:
`ISO時刻 \t 事象 \t 札 \t IP \t 分あたりの枠 \t メモ`
- 事象: `issue`（発行）または `stop`（停止）
- 札: 32 文字のランダム文字列（URL-safe）
- メモ: 改行・タブを空白に潰したメモ文字列

### 発行ページと bin/fuda の使い方

- 発行ページの住所: `https://<ホスト>/issue`。ボタンを押すと専用の接続 URL（`https://<ホスト>/mcp/<札>`）が払い出される。
- 管理ツール `bin/fuda`（公開サーバーでは `sudo -u mcp bin/fuda` で実行）:
  - 一覧表示: `bin/fuda list`（停止済みも含める場合は `--all`）
  - 手動発行: `bin/fuda issue [--memo "テスト用"]`（札の全文と、`MCP_PUBLIC_BASE` があれば完成 URL を表示）
  - 札の停止: `bin/fuda stop <札の先頭8字以上または全文>`（一意に定まらない場合は拒否）

### 旧パスの閉じ方と弱点

- 旧パスの閉じ方: `.env` に `MCP_LEGACY_PATH=0` を指定してサーバーを再起動すると、従来の共通パス（`MCP_HTTP_PATH` の値・秘密の長いパス）へのアクセスは即座に 404 となり、札付き URL のみが受け付けられるようになる（札付き URL の接頭辞 `/mcp/` とは別物）。閉じた瞬間に claude.ai に登録済みの既存コネクタは全部切れる。閉じる前に発行ページへ誘導する期間を置く。旧パスの find_combos は接続元 IP ごと 10/分。claude.ai の出口 IP は共有なので実質 30 個ほどで分け合う＝札に移る動機の一つ。
- 探りへの備え: 無い札・知らないパス・発行ページへの要求は接続元 IP の枠（60/分）で数える（超えれば 429・404 は返さない）。他所のページからの自動投稿は Sec-Fetch-Site で弾く・curl は通る＝運営の手動発行用。正しい札の呼び出しは IP の枠を使わない（claude.ai の出口 IP は利用者で共有されるため札ごとの枠だけで数える）。札は 192 ビットの乱数なので総当たりでは当たらないが、枠が無いと 404 が「無料で叩き放題の口」になる（2026-09-07 の指摘・旧 RateLimitASGI の振る舞いの復元）。 他所のページからの自動投稿は `Sec-Fetch-Site` が `cross-site` なら 403。これは今のブラウザが送るヘッダに頼った備えで、送らない相手（curl・古いブラウザ）には効かない＝運営の手動発行はそのまま通る。同じ tailnet の別ホストからは same-site になるので弾かない。
- 弱点: 接続 URL は HTTP のパスそのものであるため、Web ブラウザの履歴、プロキシやサーバーのアクセスログ、クライアント側の設定画面に残る。利用者は無くした場合は発行ページで新しく取り直すだけでよい（`bin/fuda stop` は運営が漏れた札や濫用した札を止めるための道具）。
