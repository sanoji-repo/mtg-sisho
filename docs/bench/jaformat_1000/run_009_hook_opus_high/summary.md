# 《日本語名》書式ベンチ — 100 問・run_009_hook_opus_high

pass=《》1個以上・《》の中身が全部 DB 一致・《》外に裸の英語名/日本語名なし。**改変ゼロ答案=DB から引いた名前（英語半分が DB に一致）を一字も変えていない（採点の物差し 8/22 夜）**。創作訳ゼロ答案=改変もなく DB に無い名前もない（アーキタイプ名は別勘定）。機械採点は近似（答案原文は q*.json）。

| model | effort | pass | **改変ゼロ答案** | 創作訳ゼロ答案 | 《》DB一致/《》総数 | 改変（英語半分はDB・日本語半分が違う） | 《》不一致（DBに無い名） | 《》内英語名（日本語名あり） | 《》内アーキタイプ名 | 裸英語名 | 裸日本語名 | 《》ゼロ答案 | 欠落 | ターン中央値 | 秒中央値 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| opus | high | 92/100 | **99/100** | 97/100 | 2678/2696 | 1 | 13 | 4 | 0 | 41 | 1 | 1 | 0 | 14.0 | 85.8 |

## 明細（pass 以外）

- [opus/high] q813 《》37/37 | 改変=[] | 不一致=[] | 《》内英語=[] | 裸英=['Delirium'] | 裸日=[] | turns=14 108s
- [opus/high] q825 《》12/12 | 改変=[] | 不一致=[] | 《》内英語=[] | 裸英=['Ancient Tomb', 'City of Brass', 'Forest', 'Misty Rainforest', 'Polluted Delta', 'Verdant Catacombs'] | 裸日=[] | turns=2 30s
- [opus/high] q827 《》20/20 | 改変=[] | 不一致=[] | 《》内英語=[] | 裸英=['Basking Rootwalla', 'Bloodghast', 'Bloodstained Mire', 'Chain of Vapor', 'Deathrite Shaman', 'Elvish Spirit Guide', 'Force of Vigor', 'Force of Will', 'Gemstone Caverns', 'Hollow One', 'Ingot Chewer', 'Leyline of the Void', "Mishra's Bauble", 'Ravenous Trap', 'Serum Powder', 'Simian Spirit Guide', 'Squee, Goblin Nabob', 'Squee, the Immortal', 'Street Wraith', 'Vengevine', 'Wasteland', 'Wooded Foothills'] | 裸日=[] | turns=5 80s
- [opus/high] q830 《》0/0 | 改変=[] | 不一致=[] | 《》内英語=[] | 裸英=['Gempalm Incinerator', 'Goblin Lackey', 'Goblin Matron', 'Goblin Piledriver', 'Goblin Recruiter', 'Goblin Ringleader', 'Goblin Warchief', 'Siege-Gang Commander', 'Sparksmith'] | 裸日=['ゴブリンの女看守'] | turns=1 34s
- [opus/high] q831 《》18/21 | 改変=['古えの墓/Ancient Tomb'] | 不一致=['…', '…'] | 《》内英語=[] | 裸英=[] | 裸日=[] | turns=4 89s
- [opus/high] q832 《》4/5 | 改変=[] | 不一致=['噴出の稲妻窖'] | 《》内英語=[] | 裸英=[] | 裸日=[] | turns=5 92s
- [opus/high] q887 《》33/33 | 改変=[] | 不一致=[] | 《》内英語=[] | 裸英=['Brawl'] | 裸日=[] | turns=27 164s
- [opus/high] q891 《》13/27 | 改変=[] | 不一致=['眠りへの拘束', '回転する交转子', '巻き締めの巧技', 'ニクスの祭殿、ニcredibleソス', '厳格な放逐者', '全知全能', 'ミステリーの掌握者、ジェイス', '第二の日の出への接近', '紅蓮術士の消耗', '第二の日の出への接近'] | 《》内英語=['Hidden Strings', 'Vizier of Tumbling Sands', 'Banefire', 'Pore Over the Pages'] | 裸英=['Granted', 'Lotus Field'] | 裸日=[] | turns=3 123s
