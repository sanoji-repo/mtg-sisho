# 名前忠実度ベンチ — model=opus・42 問・names_opus_20260821

| effort | 条件 | 正解 | 捏造/誤り | 不明 | 「なし」誤判定 | 欠落 | ターン中央値 | 秒中央値 | 思考tok中央値 |
|---|---|---|---|---|---|---|---|---|---|
| low | on | 42 (100%) | 0 | 0 | 0 | 0 | 3.0 | 6.5 | 0.0 |
| low | off | 12 (29%) | 20 | 9 | 1 | 0 | 1.0 | 3.3 | 0.0 |
| medium | on | 42 (100%) | 0 | 0 | 0 | 0 | 3.0 | 6.7 | 0.0 |
| medium | off | 13 (31%) | 16 | 12 | 1 | 0 | 1.0 | 5.5 | 188.0 |
| high | on | 42 (100%) | 0 | 0 | 0 | 0 | 3.0 | 6.5 | 0.0 |
| high | off | 22 (52%) | 10 | 10 | 0 | 0 | 1.0 | 10.1 | 487.5 |
| xhigh | on | 42 (100%) | 0 | 0 | 0 | 0 | 3.0 | 7.1 | 22.5 |
| xhigh | off | 17 (40%) | 11 | 13 | 1 | 0 | 1.0 | 13.6 | 748.0 |
| max | on | 42 (100%) | 0 | 0 | 0 | 0 | 3.0 | 7.1 | 58.5 |
| max | off | 17 (40%) | 14 | 11 | 0 | 0 | 1.0 | 20.2 | 1215.5 |

## カテゴリ別 正解数

| effort | 条件 | A_no_ja | B_hob | C_multi | D_famous | E_nonliteral |
|---|---|---|---|---|---|---|
| low | on | 10 | 10 | 8 | 6 | 8 |
| low | off | 1 | 1 | 1 | 5 | 4 |
| medium | on | 10 | 10 | 8 | 6 | 8 |
| medium | off | 2 | 0 | 1 | 4 | 6 |
| high | on | 10 | 10 | 8 | 6 | 8 |
| high | off | 4 | 0 | 6 | 5 | 7 |
| xhigh | on | 10 | 10 | 8 | 6 | 8 |
| xhigh | off | 3 | 0 | 2 | 6 | 6 |
| max | on | 10 | 10 | 8 | 6 | 8 |
| max | off | 4 | 0 | 2 | 6 | 5 |

## 明細（正解以外）

- [low/off] A_no_ja Afterlife Insurance | 正=日本語版なし | 答=不明 | unknown | turns=1
- [low/off] A_no_ja Ezio, Blade of Vengeance | 正=日本語版なし | 答=復讐の刃、エツィオ | wrong | turns=1
- [low/off] A_no_ja Underground Sea | 正=日本語版なし | 答=Underground Sea | wrong | turns=1
- [low/off] A_no_ja Shadow the Hedgehog | 正=日本語版なし | 答=不明 | unknown | turns=1
- [low/off] A_no_ja Nazgûl Battle-Mace | 正=日本語版なし | 答=ナズグルの戦鎚 | wrong | turns=1
- [low/off] A_no_ja Greymond, Avacyn's Stalwart | 正=日本語版なし | 答=アヴァシンの堅牢、グレイモンド | wrong | turns=1
- [low/off] A_no_ja Achilles Davenport | 正=日本語版なし | 答=アキレス・ダヴェンポート | wrong | turns=1
- [low/off] A_no_ja Aragorn, Hornburg Hero | 正=日本語版なし | 答=ホルンバーグの英雄、アラゴルン | wrong | turns=1
- [low/off] A_no_ja Rooftop Bypass | 正=日本語版なし | 答=不明 | unknown | turns=1
- [low/off] B_hob Dáin, Lord of the Iron Hills | 正=くろがね連山の統治者、ダーイン | 答=鉄の丘の王、ダイン | wrong | turns=1
- [low/off] B_hob Elven Raft-Steerer | 正=エルフのいかだの舵取り | 答=不明 | unknown | turns=1
- [low/off] B_hob Dwarven Mauler | 正=ドワーフの打ち壊し屋 | 答=ドワーフの殴り手 | wrong | turns=1
- [low/off] B_hob Goblin Plate Mail | 正=ゴブリンの板金鎧 | 答=日本語版なし | noja_wrong | turns=1
- [low/off] B_hob Wilderland Scrounger | 正=荒れ地の国のたかり屋 | 答=不明 | unknown | turns=1
- [low/off] B_hob Old Fat Spider | 正=でぶの年寄りクモ | 答=不明 | unknown | turns=1
- [low/off] B_hob The Queen of Dale | 正=谷間の国の王妃 | 答=不明 | unknown | turns=1
- [low/off] B_hob Warg Tactics | 正=魔狼の戦術 | 答=不明 | unknown | turns=1
- [low/off] B_hob Quarrel | 正=押し問答 | 答=不明 | unknown | turns=1
- [low/off] C_multi Razorgrass Ambush // Razorgrass Field | 正=剃刀草の待ち伏せ // 剃刀草の原野 | 答=剃刀草の待ち伏せ // 剃刀草の草原 | wrong | turns=1
- [low/off] C_multi Jwari Disruption // Jwari Ruins | 正=ジュワー島の撹乱 // ジュワー島の遺跡 | 答=ジュワー島の攪乱 // ジュワー島の遺跡 | wrong | turns=1
- [low/off] C_multi Wandering Archaic // Explore the Vastlands | 正=さまようアルカイック // 大界の探検 | 答=遍歴の古代種 // 大地の探索 | wrong | turns=1
- [low/off] C_multi Riverglide Pathway // Lavaglide Pathway | 正=河川滑りの小道 // 溶岩滑りの小道 | 答=河潜みの小道 // 熔滑りの小道 | wrong | turns=1
- [low/off] C_multi Waterlogged Teachings // Inundated Archive | 正=水浸しの教え // 冠水した書庫 | 答=水浸しの教え // 水没した書庫 | wrong | turns=1
- [low/off] C_multi Shatterskull Smashing // Shatterskull, the Hammer Pass | 正=髑髏砕きの一撃 // 鎚の山道、髑髏砕き | 答=髑髏砕きの一撃 // 髑髏砕きのハンマー峠 | wrong | turns=1
- [low/off] C_multi Etali, Primal Conqueror // Etali, Primal Sickness | 正=原初の征服者、エターリ // 原初の病、エターリ | 答=原初の征服者、エターリ // 原初の疫病、エターリ | wrong | turns=1
- [low/off] D_famous Deadly Rollick | 正=致命的なはしゃぎ回り | 答=必殺の一撃 | wrong | turns=1
- [low/off] E_nonliteral Cori-Steel Cutter | 正=コーリ鋼の短刀 | 答=コリ鋼の切断者 | wrong | turns=1
- [low/off] E_nonliteral Psychic Frog | 正=超能力蛙 | 答=精神接続のカエル | wrong | turns=1
- [low/off] E_nonliteral Ocelot Pride | 正=オセロットの群れ | 答=オセロットの誇り | wrong | turns=1
- [low/off] E_nonliteral Phlage, Titan of Fire's Fury | 正=火の怒りのタイタン、フレージ | 答=業火のタイタン、フレイジ | wrong | turns=1
- [medium/off] A_no_ja Afterlife Insurance | 正=日本語版なし | 答=不明 | unknown | turns=1
- [medium/off] A_no_ja Ezio, Blade of Vengeance | 正=日本語版なし | 答=復讐の刃、エツィオ | wrong | turns=1
- [medium/off] A_no_ja Underground Sea | 正=日本語版なし | 答=地底の大河 | wrong | turns=1
- [medium/off] A_no_ja Nazgûl Battle-Mace | 正=日本語版なし | 答=ナズグルの戦棍 | wrong | turns=1
- [medium/off] A_no_ja Greymond, Avacyn's Stalwart | 正=日本語版なし | 答=アヴァシンの忠臣、グレイモンド | wrong | turns=1
- [medium/off] A_no_ja Achilles Davenport | 正=日本語版なし | 答=アキレス・ダヴェンポート | wrong | turns=1
- [medium/off] A_no_ja Aragorn, Hornburg Hero | 正=日本語版なし | 答=角笛城の英雄、アラゴルン | wrong | turns=1
- [medium/off] A_no_ja Rooftop Bypass | 正=日本語版なし | 答=不明 | unknown | turns=1
- [medium/off] B_hob Large Bear | 正=大きな熊 | 答=不明 | unknown | turns=1
- [medium/off] B_hob Dáin, Lord of the Iron Hills | 正=くろがね連山の統治者、ダーイン | 答=鉄の丘の主、ダイン | wrong | turns=1
- [medium/off] B_hob Elven Raft-Steerer | 正=エルフのいかだの舵取り | 答=不明 | unknown | turns=1
- [medium/off] B_hob Dwarven Mauler | 正=ドワーフの打ち壊し屋 | 答=不明 | unknown | turns=1
- [medium/off] B_hob Goblin Plate Mail | 正=ゴブリンの板金鎧 | 答=日本語版なし | noja_wrong | turns=1
- [medium/off] B_hob Wilderland Scrounger | 正=荒れ地の国のたかり屋 | 答=不明 | unknown | turns=1
- [medium/off] B_hob Old Fat Spider | 正=でぶの年寄りクモ | 答=不明 | unknown | turns=1
- [medium/off] B_hob The Queen of Dale | 正=谷間の国の王妃 | 答=不明 | unknown | turns=1
- [medium/off] B_hob Warg Tactics | 正=魔狼の戦術 | 答=不明 | unknown | turns=1
- [medium/off] B_hob Quarrel | 正=押し問答 | 答=不明 | unknown | turns=1
- [medium/off] C_multi Razorgrass Ambush // Razorgrass Field | 正=剃刀草の待ち伏せ // 剃刀草の原野 | 答=剃刀草の待ち伏せ // 剃刀草の草原 | wrong | turns=1
- [medium/off] C_multi Jwari Disruption // Jwari Ruins | 正=ジュワー島の撹乱 // ジュワー島の遺跡 | 答=ジュワー島の攪乱 // ジュワー島の遺跡 | wrong | turns=1
- [medium/off] C_multi Wandering Archaic // Explore the Vastlands | 正=さまようアルカイック // 大界の探検 | 答=不明 | unknown | turns=1
- [medium/off] C_multi Riverglide Pathway // Lavaglide Pathway | 正=河川滑りの小道 // 溶岩滑りの小道 | 答=川滑りの小道 // 溶岩滑りの小道 | wrong | turns=1
- [medium/off] C_multi Waterlogged Teachings // Inundated Archive | 正=水浸しの教え // 冠水した書庫 | 答=不明 | unknown | turns=1
- [medium/off] C_multi Shatterskull Smashing // Shatterskull, the Hammer Pass | 正=髑髏砕きの一撃 // 鎚の山道、髑髏砕き | 答=髑髏砕きの一撃 // 髑髏砕き、ハンマー峠 | wrong | turns=1
- [medium/off] C_multi Etali, Primal Conqueror // Etali, Primal Sickness | 正=原初の征服者、エターリ // 原初の病、エターリ | 答=答: 原初の征服者、エターリ // 原初の病、エターリ | wrong | turns=1
- [medium/off] D_famous Garruk's Uprising | 正=ガラクの蜂起 | 答=ガラクの先触れ | wrong | turns=1
- [medium/off] D_famous Deadly Rollick | 正=致命的なはしゃぎ回り | 答=殺しの大立ち回り | wrong | turns=1
- [medium/off] E_nonliteral Cori-Steel Cutter | 正=コーリ鋼の短刀 | 答=コリ鋼の切断者 | wrong | turns=1
- [medium/off] E_nonliteral Phlage, Titan of Fire's Fury | 正=火の怒りのタイタン、フレージ | 答=火の怒りのタイタン、フレイジ | wrong | turns=1
- [high/off] A_no_ja Ezio, Blade of Vengeance | 正=日本語版なし | 答=復讐の刃、エツィオ | wrong | turns=1
- [high/off] A_no_ja Nazgûl Battle-Mace | 正=日本語版なし | 答=ナズグルの戦棍 | wrong | turns=1
- [high/off] A_no_ja Greymond, Avacyn's Stalwart | 正=日本語版なし | 答=アヴァシンの忠臣、グレイモンド | wrong | turns=1
- [high/off] A_no_ja Achilles Davenport | 正=日本語版なし | 答=アキレス・ダヴェンポート | wrong | turns=1
- [high/off] A_no_ja Aragorn, Hornburg Hero | 正=日本語版なし | 答=角笛城の英雄、アラゴルン | wrong | turns=1
- [high/off] A_no_ja Rooftop Bypass | 正=日本語版なし | 答=不明 | unknown | turns=1
- [high/off] B_hob Large Bear | 正=大きな熊 | 答=不明 | unknown | turns=1
- [high/off] B_hob Dáin, Lord of the Iron Hills | 正=くろがね連山の統治者、ダーイン | 答=鉄の丘の王、ダーイン | wrong | turns=1
- [high/off] B_hob Elven Raft-Steerer | 正=エルフのいかだの舵取り | 答=不明 | unknown | turns=1
- [high/off] B_hob Dwarven Mauler | 正=ドワーフの打ち壊し屋 | 答=不明 | unknown | turns=1
- [high/off] B_hob Goblin Plate Mail | 正=ゴブリンの板金鎧 | 答=不明 | unknown | turns=1
- [high/off] B_hob Wilderland Scrounger | 正=荒れ地の国のたかり屋 | 答=不明 | unknown | turns=1
- [high/off] B_hob Old Fat Spider | 正=でぶの年寄りクモ | 答=不明 | unknown | turns=1
- [high/off] B_hob The Queen of Dale | 正=谷間の国の王妃 | 答=不明 | unknown | turns=1
- [high/off] B_hob Warg Tactics | 正=魔狼の戦術 | 答=不明 | unknown | turns=1
- [high/off] B_hob Quarrel | 正=押し問答 | 答=不明 | unknown | turns=1
- [high/off] C_multi Wandering Archaic // Explore the Vastlands | 正=さまようアルカイック // 大界の探検 | 答=遍歴の古きもの // 広大な地の探索 | wrong | turns=1
- [high/off] C_multi Riverglide Pathway // Lavaglide Pathway | 正=河川滑りの小道 // 溶岩滑りの小道 | 答=川滑りの小道 // 溶岩滑りの小道 | wrong | turns=1
- [high/off] D_famous Deadly Rollick | 正=致命的なはしゃぎ回り | 答=命取りの享楽 | wrong | turns=1
- [high/off] E_nonliteral Phlage, Titan of Fire's Fury | 正=火の怒りのタイタン、フレージ | 答=炎の怒りのタイタン、フレイジ | wrong | turns=1
- [xhigh/off] A_no_ja Afterlife Insurance | 正=日本語版なし | 答=不明 | unknown | turns=1
- [xhigh/off] A_no_ja Ezio, Blade of Vengeance | 正=日本語版なし | 答=復讐の刃、エツィオ | wrong | turns=1
- [xhigh/off] A_no_ja Nazgûl Battle-Mace | 正=日本語版なし | 答=ナズグルの戦鎚矛 | wrong | turns=1
- [xhigh/off] A_no_ja Greymond, Avacyn's Stalwart | 正=日本語版なし | 答=不明 | unknown | turns=2
- [xhigh/off] A_no_ja Achilles Davenport | 正=日本語版なし | 答=不明 | unknown | turns=1
- [xhigh/off] A_no_ja Aragorn, Hornburg Hero | 正=日本語版なし | 答=角笛城の英雄、アラゴルン | wrong | turns=1
- [xhigh/off] A_no_ja Rooftop Bypass | 正=日本語版なし | 答=不明 | unknown | turns=1
- [xhigh/off] B_hob Large Bear | 正=大きな熊 | 答=不明 | unknown | turns=1
- [xhigh/off] B_hob Dáin, Lord of the Iron Hills | 正=くろがね連山の統治者、ダーイン | 答=鉄の丘陵の王、ダーイン | wrong | turns=1
- [xhigh/off] B_hob Elven Raft-Steerer | 正=エルフのいかだの舵取り | 答=不明 | unknown | turns=1
- [xhigh/off] B_hob Dwarven Mauler | 正=ドワーフの打ち壊し屋 | 答=不明 | unknown | turns=2
- [xhigh/off] B_hob Goblin Plate Mail | 正=ゴブリンの板金鎧 | 答=日本語版なし | noja_wrong | turns=1
- [xhigh/off] B_hob Wilderland Scrounger | 正=荒れ地の国のたかり屋 | 答=不明 | unknown | turns=2
- [xhigh/off] B_hob Old Fat Spider | 正=でぶの年寄りクモ | 答=不明 | unknown | turns=1
- [xhigh/off] B_hob The Queen of Dale | 正=谷間の国の王妃 | 答=不明 | unknown | turns=1
- [xhigh/off] B_hob Warg Tactics | 正=魔狼の戦術 | 答=不明 | unknown | turns=1
- [xhigh/off] B_hob Quarrel | 正=押し問答 | 答=不明 | unknown | turns=1
- [xhigh/off] C_multi Jwari Disruption // Jwari Ruins | 正=ジュワー島の撹乱 // ジュワー島の遺跡 | 答=ジュワー島の攪乱 // ジュワー島の遺跡 | wrong | turns=1
- [xhigh/off] C_multi Wandering Archaic // Explore the Vastlands | 正=さまようアルカイック // 大界の探検 | 答=不明 | unknown | turns=1
- [xhigh/off] C_multi Riverglide Pathway // Lavaglide Pathway | 正=河川滑りの小道 // 溶岩滑りの小道 | 答=川滑りの小道 // 溶岩滑りの小道 | wrong | turns=1
- [xhigh/off] C_multi Waterlogged Teachings // Inundated Archive | 正=水浸しの教え // 冠水した書庫 | 答=水浸しの教え // 水没した書庫 | wrong | turns=1
- [xhigh/off] C_multi Shatterskull Smashing // Shatterskull, the Hammer Pass | 正=髑髏砕きの一撃 // 鎚の山道、髑髏砕き | 答=髑髏砕きの一撃 // 髑髏砕き、ハンマー峠 | wrong | turns=1
- [xhigh/off] C_multi Etali, Primal Conqueror // Etali, Primal Sickness | 正=原初の征服者、エターリ // 原初の病、エターリ | 答=原初の征服者、エターリ // 原初の疫病、エターリ | wrong | turns=1
- [xhigh/off] E_nonliteral Cori-Steel Cutter | 正=コーリ鋼の短刀 | 答=コリ鋼の短刀 | wrong | turns=1
- [xhigh/off] E_nonliteral Phlage, Titan of Fire's Fury | 正=火の怒りのタイタン、フレージ | 答=炎の怒りのタイタン、フレイジ | wrong | turns=1
- [max/off] A_no_ja Ezio, Blade of Vengeance | 正=日本語版なし | 答=復讐の刃、エツィオ | wrong | turns=1
- [max/off] A_no_ja Nazgûl Battle-Mace | 正=日本語版なし | 答=ナズグルの戦棍 | wrong | turns=1
- [max/off] A_no_ja Greymond, Avacyn's Stalwart | 正=日本語版なし | 答=不明 | unknown | turns=3
- [max/off] A_no_ja Achilles Davenport | 正=日本語版なし | 答=アキレス・ダヴェンポート | wrong | turns=1
- [max/off] A_no_ja Aragorn, Hornburg Hero | 正=日本語版なし | 答=角笛城の英雄、アラゴルン | wrong | turns=1
- [max/off] A_no_ja Rooftop Bypass | 正=日本語版なし | 答=不明 | unknown | turns=1
- [max/off] B_hob Large Bear | 正=大きな熊 | 答=不明 | unknown | turns=1
- [max/off] B_hob Dáin, Lord of the Iron Hills | 正=くろがね連山の統治者、ダーイン | 答=鉄の丘の王、ダイン | wrong | turns=1
- [max/off] B_hob Elven Raft-Steerer | 正=エルフのいかだの舵取り | 答=不明 | unknown | turns=1
- [max/off] B_hob Dwarven Mauler | 正=ドワーフの打ち壊し屋 | 答=不明 | unknown | turns=3
- [max/off] B_hob Goblin Plate Mail | 正=ゴブリンの板金鎧 | 答=不明 | unknown | turns=2
- [max/off] B_hob Wilderland Scrounger | 正=荒れ地の国のたかり屋 | 答=不明 | unknown | turns=1
- [max/off] B_hob Old Fat Spider | 正=でぶの年寄りクモ | 答=不明 | unknown | turns=1
- [max/off] B_hob The Queen of Dale | 正=谷間の国の王妃 | 答=不明 | unknown | turns=1
- [max/off] B_hob Warg Tactics | 正=魔狼の戦術 | 答=不明 | unknown | turns=1
- [max/off] B_hob Quarrel | 正=押し問答 | 答=不明 | unknown | turns=2
- [max/off] C_multi Thousand Moons Smithy // Barracks of the Thousand | 正=千の月の鍛冶場 // 千の兵舎 | 答=千の月の鍛冶場 // 千人隊の兵舎 | wrong | turns=1
- [max/off] C_multi Wandering Archaic // Explore the Vastlands | 正=さまようアルカイック // 大界の探検 | 答=放浪する古老 // 広漠なる地の探検 | wrong | turns=3
- [max/off] C_multi Riverglide Pathway // Lavaglide Pathway | 正=河川滑りの小道 // 溶岩滑りの小道 | 答=川滑りの小道 // 溶岩滑りの小道 | wrong | turns=1
- [max/off] C_multi Waterlogged Teachings // Inundated Archive | 正=水浸しの教え // 冠水した書庫 | 答=水没した教え // 水浸しの書庫 | wrong | turns=1
- [max/off] C_multi Shatterskull Smashing // Shatterskull, the Hammer Pass | 正=髑髏砕きの一撃 // 鎚の山道、髑髏砕き | 答=髑髏砕きの一撃 // 槌の山道、髑髏砕き | wrong | turns=1
- [max/off] C_multi Etali, Primal Conqueror // Etali, Primal Sickness | 正=原初の征服者、エターリ // 原初の病、エターリ | 答=原初の征服者、エターリ // 原初の疫病、エターリ | wrong | turns=1
- [max/off] E_nonliteral Cori-Steel Cutter | 正=コーリ鋼の短刀 | 答=コリ鋼の短刀 | wrong | turns=1
- [max/off] E_nonliteral Psychic Frog | 正=超能力蛙 | 答=精神カエル | wrong | turns=1
- [max/off] E_nonliteral Phlage, Titan of Fire's Fury | 正=火の怒りのタイタン、フレージ | 答=炎の怒りのタイタン、フレイジ | wrong | turns=1
