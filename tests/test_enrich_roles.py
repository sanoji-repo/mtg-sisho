#!/usr/bin/env python3
"""役割の列（除去 removal / removal_types・ドロー draw_count / draw_x）を導く関数の試験。

DB には繋がない。parse（enrich_removal.py）と parse_draw（enrich_draw.py）に、錨のカード（期待する結果を決めてある代表のカード）の
本文（oracle_text を一字も変えずに写したもの）を直接渡して確かめる。
「付ける錨」と「付けない錨」の両方を置く（付けない側が壊れると、別の役割に偽陽性が出る）。

走らせ方: pytest tests/test_enrich_roles.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from enrich_draw import parse_draw  # noqa: E402
from enrich_removal import parse  # noqa: E402

# 錨のカードの本文（mtg_cards_v2.oracle_text をそのまま写した物）
CARDS = {
    "Bolg of the North": "When Bolg enters, you may sacrifice another creature. When you do, Bolg deals damage equal to that creature's power to another target creature. If excess damage was dealt this way, amass Goblins X, where X is that excess damage. (Put X +1/+1 counters on an Army you control. It's also a Goblin. If you don't control an Army, create a 0/0 black Goblin Army creature token first.)",
    "Rabid Bite": "Target creature you control deals damage equal to its power to target creature you don't control.",
    "Prey Upon": "Target creature you control fights target creature you don't control. (Each deals damage equal to its power to the other.)",
    "Lightning Bolt": "Lightning Bolt deals 3 damage to any target.",
    "Anger of the Gods": "Anger of the Gods deals 3 damage to each creature. If a creature dealt damage this way would die this turn, exile it instead.",
    "Unholy Heat": "Unholy Heat deals 2 damage to target creature or planeswalker.\nDelirium — Unholy Heat deals 6 damage instead if there are four or more card types among cards in your graveyard.",
    "Orcish Bowmasters": "Flash\nWhen this creature enters and whenever an opponent draws a card except the first one they draw in each of their draw steps, this creature deals 1 damage to any target. Then amass Orcs 1.",
    "Adarkar Wastes": "{T}: Add {C}.\n{T}: Add {W} or {U}. This land deals 1 damage to you.",
    "Balin, Loremaster": "Storied (If you control three or more artifacts, legendaries, and/or Sagas, you have an enduring story for the rest of the game.)\nWhenever Balin or another Dwarf you control enters, you may discard your hand. Draw X cards, where X is the number of cards discarded this way. If you have an enduring story, Balin deals X damage to each opponent.",
    "Mana Crypt": "At the beginning of your upkeep, flip a coin. If you lose the flip, this artifact deals 3 damage to you.\n{T}: Add {C}{C}.",
    "Guttersnipe": "Whenever you cast an instant or sorcery spell, this creature deals 2 damage to each opponent.",
    "Juzám Djinn": "At the beginning of your upkeep, this creature deals 1 damage to you.",
    "Bilbo's Gambit": "Gift a Treasure (You may promise an opponent a gift as you cast this spell. If you do, they create a Treasure token before its other effects. It's an artifact with \"{T}, Sacrifice this token: Add one mana of any color.\")\nReturn target spell to its owner's hand. If the gift was promised, players can't cast spells this turn.",
    "Unsummon": "Return target creature to its owner's hand.",
    "Vapor Snag": "Return target creature to its owner's hand. Its controller loses 1 life.",
    "Ephemerate": "Exile target creature you control, then return it to the battlefield under its owner's control.\nRebound (If you cast this spell from your hand, exile it as it resolves. At the beginning of your next upkeep, you may cast this card from exile without paying its mana cost.)",
    "Flickerwisp": "Flying\nWhen this creature enters, exile another target permanent. Return that card to the battlefield under its owner's control at the beginning of the next end step.",
    "Roll-Roll-Roll-Roll": "(As this Saga enters and after your draw step, add a lore counter. Sacrifice after IV.)\nI, II, III, IV — Exile up to one target creature or land you control. If you do, return it to the battlefield under its owner's control at the beginning of the next end step.",
    "Restoration Angel": "Flash\nFlying\nWhen this creature enters, you may exile target non-Angel creature you control, then return that card to the battlefield under your control.",
    "Cloudshift": "Exile target creature you control, then return that card to the battlefield under your control.",
    "Felidar Guardian": "When this creature enters, you may exile another target permanent you control, then return that card to the battlefield under its owner's control.",
    "Journey to Nowhere": "When this enchantment enters, exile target creature.\nWhen this enchantment leaves the battlefield, return the exiled card to the battlefield under its owner's control.",
    "Banishing Light": "When this enchantment enters, exile target nonland permanent an opponent controls until this enchantment leaves the battlefield.",
    "Pacifism": "Enchant creature\nEnchanted creature can't attack or block.",
    "Arrest": "Enchant creature\nEnchanted creature can't attack or block, and its activated abilities can't be activated.",
    "Claustrophobia": "Enchant creature\nWhen this Aura enters, tap enchanted creature.\nEnchanted creature doesn't untap during its controller's untap step.",
    "Enchanted River's Grasp": "Enchant creature\nWhen this Aura enters, tap enchanted creature and remove all counters from it.\nEnchanted creature loses all abilities and doesn't untap during its controller's untap step.",
    "Kenrith's Transformation": "Enchant creature\nWhen this Aura enters, draw a card.\nEnchanted creature loses all abilities and is a green Elk creature with base power and toughness 3/3. (It loses all other card types and creature types.)",
    "Darksteel Mutation": "Enchant creature\nEnchanted creature is an Insect artifact creature with base power and toughness 0/1 and has indestructible, and it loses all other abilities, card types, and creature types.",
    "Rancor": "Enchant creature\nEnchanted creature gets +2/+0 and has trample.\nWhen this Aura is put into a graveyard from the battlefield, return it to its owner's hand.",
    "Holy Strength": "Enchant creature\nEnchanted creature gets +1/+2.",
    "Moonshadow": "Menace\nThis creature enters with six -1/-1 counters on it.\nWhenever one or more permanent cards are put into your graveyard from anywhere while this creature has a -1/-1 counter on it, remove a -1/-1 counter from this creature.",
    "Devoted Druid": "{T}: Add {G}.\nPut a -1/-1 counter on this creature: Untap this creature.",
    "Wall of Roots": "Defender\nPut a -0/-1 counter on this creature: Add {G}. Activate only once each turn.",
    "Toxic Deluge": "As an additional cost to cast this spell, pay X life.\nAll creatures get -X/-X until end of turn.",
    "Glistening Deluge": "All creatures get -1/-1 until end of turn. Creatures that are green and/or white get an additional -2/-2 until end of turn.",
    "Dismember": "({B/P} can be paid with either {B} or 2 life.)\nTarget creature gets -5/-5 until end of turn.",
    "Skinrender": "When this creature enters, put three -1/-1 counters on target creature.",
    "Relic of Progenitus": "{T}: Target player exiles a card from their graveyard.\n{1}, Exile this artifact: Exile all graveyards. Draw a card.",
    "Farewell": "Choose one or more —\n• Exile all artifacts.\n• Exile all creatures.\n• Exile all enchantments.\n• Exile all graveyards.",
    "Bojuka Bog": "This land enters tapped.\nWhen this land enters, exile target player's graveyard.\n{T}: Add {B}.",
    "Fatal Push": "Destroy target creature if it has mana value 2 or less.\nRevolt — Destroy that creature if it has mana value 4 or less instead if a permanent left the battlefield under your control this turn.",
    "Swords to Plowshares": "Exile target creature. Its controller gains life equal to its power.",
    "Wrath of God": "Destroy all creatures. They can't be regenerated.",
    "Liliana of the Veil": "+1: Each player discards a card.\n−2: Target player sacrifices a creature.\n−6: Separate all permanents target player controls into two piles. That player sacrifices all permanents in the pile of their choice.",
    "Brainstorm": "Draw three cards, then put two cards from your hand on top of your library in any order.",
    "Faithless Looting": "Draw two cards, then discard two cards.\nFlashback {2}{R} (You may cast this card from your graveyard for its flashback cost. Then exile it.)",
    "Consider": "Surveil 1. (Look at the top card of your library. You may put it into your graveyard.)\nDraw a card.",
    "Mishra's Bauble": "{T}, Sacrifice this artifact: Look at the top card of target player's library. Draw a card at the beginning of the next turn's upkeep.",
    "Fear of Missing Out": "When this creature enters, discard a card, then draw a card.\nDelirium — Whenever this creature attacks for the first time each turn, if there are four or more card types among cards in your graveyard, untap target creature. After this phase, there is an additional combat phase.",
    "Thoughtseize": "Target player reveals their hand. You choose a nonland card from it. That player discards that card. You lose 2 life.",
    "Street Wraith": "Swampwalk (This creature can't be blocked as long as defending player controls a Swamp.)\nCycling—Pay 2 life. (Pay 2 life, Discard this card: Draw a card.)",
    "Raffine's Informant": "When this creature enters, it connives. (Draw a card, then discard a card. If you discarded a nonland card, put a +1/+1 counter on this creature.)",
    "Patient Instructor": "Vigilance\nWhen this creature enters, recruit. (Draw a card, then discard a card. If you discarded a nonland card, create a 1/1 white Human Soldier creature token.)",
    "Great Gilded Boat": "Whenever you attack, recruit. (Draw a card, then discard a card. If you discarded a nonland card, create a 1/1 white Human Soldier creature token.)\nCrew 2 (Tap any number of creatures you control with total power 2 or more: This Vehicle becomes an artifact creature until end of turn.)",
    "Troll of Khazad-dûm": "This creature can't be blocked except by three or more creatures.\nSwampcycling {1} ({1}, Discard this card: Search your library for a Swamp card, reveal it, put it into your hand, then shuffle.)",
    "Tireless Tracker": "Landfall — Whenever a land you control enters, investigate. (Create a Clue token. It's an artifact with \"{2}, Sacrifice this token: Draw a card.\")\nWhenever you sacrifice a Clue, put a +1/+1 counter on this creature.",
    "Hieroglyphic Illumination": "Draw two cards.\nCycling {U} ({U}, Discard this card: Draw a card.)",
    "Fluctuator": "Cycling abilities you activate cost {2} less to activate.",
    "Glorious Purpose": "Whenever a creature you control connives, put a +1/+1 counter on that creature and a plan counter on this enchantment.\nWhen the sixth plan counter is put on this enchantment, sacrifice it. If you do, exile the top four cards of your library. You may cast any number of spells from among them without paying their mana costs. Put the rest into your hand.",
    "Recruit Instructor": "Whenever Recruit Instructor attacks, draft a card from Recruit Instructor's spellbook.\nValiant — Whenever Recruit Instructor becomes the target of a spell or ability you control for the first time each turn, create a 1/1 white Mouse creature token.",
    "Burst Lightning": "Kicker {4} (You may pay an additional {4} as you cast this spell.)\nBurst Lightning deals 2 damage to any target. If this spell was kicked, it deals 4 damage instead.",
    "Venser, Shaper Savant": "Flash (You may cast this spell any time you could cast an instant.)\nWhen Venser enters, return target spell or permanent to its owner's hand.",
    "Hullbreaker Horror": "Flash\nThis spell can't be countered.\nWhenever you cast a spell, choose up to one —\n• Return target spell you don't control to its owner's hand.\n• Return target nonland permanent to its owner's hand.",
    "Agony Warp": "Target creature gets -3/-0 until end of turn.\nTarget creature gets -0/-3 until end of turn.",
    "Faith's Fetters": "Enchant permanent\nWhen this Aura enters, you gain 4 life.\nEnchanted permanent can't attack or block, and its activated abilities can't be activated unless they're mana abilities.",
    "Oppressive Rays": "Enchant creature\nEnchanted creature can't attack or block unless its controller pays {3}.\nActivated abilities of enchanted creature cost {3} more to activate.",
    "Aether Flash": "Whenever a creature enters, this enchantment deals 2 damage to it.",
    "Parting Gust": "Gift a tapped Fish (You may promise an opponent a gift as you cast this spell. If you do, they create a tapped 1/1 blue Fish creature token before its other effects.)\nExile target nontoken creature. If the gift wasn't promised, return that card to the battlefield under its owner's control with a +1/+1 counter on it at the beginning of the next end step.",
    "Raffine, Scheming Seer": "Flying, ward {1}\nWhenever you attack, target attacking creature connives X, where X is the number of attacking creatures. (Draw X cards, then discard X cards. Put a +1/+1 counter on that creature for each nonland card discarded this way.)",
    "Retribution": "Choose two target creatures controlled by the same opponent. That player chooses and sacrifices one of those creatures. Put a -1/-1 counter on the other.",
    "Contagion": "You may pay 1 life and exile a black card from your hand rather than pay this spell's mana cost.\nDistribute two -2/-1 counters among one or two target creatures.",
    "Petrify": "Enchant artifact or creature\nEnchanted permanent can't attack or block, and its activated abilities can't be activated.",
    "Girder Goons": "When this creature dies, create a tapped 2/2 black Rogue creature token.\nBlitz {3}{B} (If you cast this spell for its blitz cost, it gains haste and \"When this creature dies, draw a card.\" Sacrifice it at the beginning of the next end step.)",
    "Henzie \"Toolbox\" Torre": "Each creature spell you cast with mana value 4 or greater has blitz. The blitz cost is equal to its mana cost. (You may choose to cast that spell for its blitz cost. If you do, it gains haste and \"When this creature dies, draw a card.\" Sacrifice it at the beginning of the next end step.)\nBlitz costs you pay cost {1} less for each time you've cast your commander from the command zone this game.",
    "Tenacious Underdog": "Blitz—{2}{B}{B}, Pay 2 life. (If you cast this spell for its blitz cost, it gains haste and \"When this creature dies, draw a card.\" Sacrifice it at the beginning of the next end step.)\nYou may cast this card from your graveyard using its blitz ability.",
    "Clutch of Undeath": "Enchant creature\nEnchanted creature gets +3/+3 as long as it's a Zombie. Otherwise, it gets -3/-3.",
    "Gift of Fangs": "Enchant creature\nEnchanted creature gets +2/+2 as long as it's a Vampire. Otherwise, it gets -2/-2.",
    "Nyx Infusion": "Enchant creature\nEnchanted creature gets +2/+2 as long as it's an enchantment. Otherwise, it gets -2/-2.",
    "Serra's Boon": "Enchant creature\nEnchanted creature gets +1/+2 as long as it's white. Otherwise, it gets -2/-1.",
    "Tahngarth's Rage": "Enchant creature\nEnchanted creature gets +3/+0 as long as it's attacking. Otherwise, it gets -2/-1.",
    "Abyssal Harvester": "{T}: Exile target creature card from a graveyard that was put there this turn. Create a token that's a copy of it, except it's a Nightmare in addition to its other types. Then exile all other Nightmare tokens you control.",
    "Unyielding Gatekeeper": "Disguise {1}{W} (You may cast this card face down for {3} as a 2/2 creature with ward {2}. Turn it face up any time for its disguise cost.)\nWhen this creature is turned face up, exile another target nonland permanent. If you controlled it, return it to the battlefield tapped. Otherwise, its controller creates a 2/2 white and blue Detective creature token.",
    "Vindictive Triumph": "Exile target creature or planeswalker. If that permanent's mana value was 3 or less, return it to the battlefield tapped under your control. Exile it at the beginning of the next end step.",
    "Koya, Death from Above": "Flying\nWhen Koya enters, exile up to one other target creature. At the beginning of the next end step, you may pay {3}{B}. If you don't, return that card to the battlefield under its owner's control.",
    "Gilraen, Dúnedain Protector": "{2}, {T}: Exile another target creature you control. You may return that card to the battlefield under its owner's control. If you don't, at the beginning of the next end step, return that card to the battlefield under its owner's control with a vigilance counter and a lifelink counter on it.",
    "Hallowed Respite": "Exile target nonlegendary creature, then return it to the battlefield under its owner's control. If it entered under your control, put a +1/+1 counter on it. Otherwise, tap it.\nFlashback {1}{W}{U} (You may cast this card from your graveyard for its flashback cost. Then exile it.)",
    "Phelia, Exuberant Shepherd": "Flash\nWhenever Phelia attacks, exile up to one other target nonland permanent. At the beginning of the next end step, return that card to the battlefield under its owner's control. If it entered under your control, put a +1/+1 counter on Phelia.",
    "Eldrazi Confluence": "Choose three. You may choose the same mode more than once.\n• Target creature gets +3/-3 until end of turn.\n• Exile target nonland permanent, then return it to the battlefield tapped under its owner's control.\n• Create a 1/1 colorless Eldrazi Scion creature token with \"Sacrifice this token: Add {C}.\"",
    "Dead Weight": "Enchant creature\nEnchanted creature gets -2/-2.",
    "Galvanic Discharge": "Choose target creature or planeswalker. You get {E}{E}{E} (three energy counters), then you may pay any amount of {E}. Galvanic Discharge deals that much damage to that permanent.",
    "Harnessed Lightning": "Choose target creature. You get {E}{E}{E} (three energy counters), then you may pay any amount of {E}. Harnessed Lightning deals that much damage to that creature.",
    "Eye for an Eye": "The next time a source of your choice would deal damage to you this turn, instead that source deals that much damage to you and Eye for an Eye deals that much damage to that source's controller.",
    "Repercussion": "Whenever a creature is dealt damage, this enchantment deals that much damage to that creature's controller.",
    "Spiteful Sliver": "Sliver creatures you control have \"Whenever this creature is dealt damage, it deals that much damage to target player or planeswalker.\"",
    "Pyromancer's Gauntlet": "If a red instant or sorcery spell you control or a red planeswalker you control would deal damage to a permanent or player, it deals that much damage plus 2 to that permanent or player instead.",
    "Mogg Maniac": "Whenever this creature is dealt damage, it deals that much damage to target opponent or planeswalker.",
    "Jackal Pup": "Whenever this creature is dealt damage, it deals that much damage to you.",
    "Justice": "At the beginning of your upkeep, sacrifice this enchantment unless you pay {W}{W}.\nWhenever a red creature or spell deals damage, this enchantment deals that much damage to that creature's or spell's controller.",
    "Boros Reckoner": "Whenever this creature is dealt damage, it deals that much damage to any target.\n{R/W}: This creature gains first strike until end of turn.",
    "Elektra, Daughter of the Hand": "Sneak {1}{B}{B} (You may cast this spell for {1}{B}{B} if you also return an unblocked attacker you control to hand during the declare blockers step. She enters tapped and attacking.)\nWhen Elektra enters, destroy target creature an opponent controls with power 3 or less.",
    "Devourer of Destiny": "You may reveal this card from your opening hand. If you do, at the beginning of your first upkeep, look at the top four cards of your library. You may put one of those cards back on top of your library. Exile the rest.\nWhen you cast this spell, exile target permanent that's one or more colors.",
    "Ugin, Eye of the Storms": "When you cast this spell, exile up to one target permanent that's one or more colors.\nWhenever you cast a colorless spell, exile up to one target permanent that's one or more colors.\n+2: You gain 3 life and draw a card.\n0: Add {C}{C}{C}.\n−11: Search your library for any number of colorless nonland cards, exile them, then shuffle. Until end of turn, you may cast those cards without paying their mana costs.",
    "Eiganjo, Seat of the Empire": "{T}: Add {W}.\nChannel — {2}{W}, Discard this card: It deals 4 damage to target attacking or blocking creature. This ability costs {1} less to activate for each legendary creature you control.",
    "Go for the Throat": "Destroy target nonartifact creature.",
    "Prismatic Ending": "Converge — Exile target nonland permanent if its mana value is less than or equal to the number of colors of mana spent to cast this spell.",
    "Fiery Impulse": "Fiery Impulse deals 2 damage to target creature.\nSpell mastery — If there are two or more instant and/or sorcery cards in your graveyard, Fiery Impulse deals 3 damage instead.",
    "Omnath, Locus of Creation": "When Omnath enters, draw a card.\nLandfall — Whenever a land you control enters, you gain 4 life if this is the first time this ability has resolved this turn. If it's the second time, add {R}{G}{W}{U}. If it's the third time, Omnath deals 4 damage to each opponent and each planeswalker you don't control.",
    "Aven Interrupter": "Flash\nFlying\nWhen this creature enters, exile target spell. It becomes plotted. (Its owner may cast it as a sorcery on a later turn without paying its mana cost.)\nSpells your opponents cast from graveyards or from exile cost {2} more to cast.",
    "Spell Queller": "Flash\nFlying\nWhen this creature enters, exile target spell with mana value 4 or less.\nWhen this creature leaves the battlefield, the exiled card's owner may cast that card without paying its mana cost.",
    "Mortician Beetle": "Whenever a player sacrifices a creature, you may put a +1/+1 counter on this creature.",
    "Mayhem Devil": "Whenever a player sacrifices a permanent, this creature deals 1 damage to any target.",
    "Hellrider": "Haste\nWhenever a creature you control attacks, this creature deals 1 damage to the player or planeswalker it's attacking.",
    "Urza's Ruinous Blast": "(You may cast a legendary sorcery only if you control a legendary creature or planeswalker.)\nExile all nonland permanents that aren't legendary.",
}


def types(name):
    """removal_types（無ければ空の list）"""
    return parse(CARDS[name])[2] or []


def entries(name, typ):
    return [e for e in (parse(CARDS[name])[3] or []) if e["type"] == typ]


# ---------------------------------------------------------------- 除去

@pytest.mark.parametrize("name", ["Bolg of the North", "Rabid Bite"])
def test_bite_is_damage_removal(name):
    """噛みつき（deals damage equal to … to target creature）は対象を取るダメージの除去"""
    es = entries(name, "damage")
    assert len(es) == 1
    assert es[0]["targeted"] is True
    assert es[0]["amount"] is None  # 本文に数字が無い＝不在は NULL


def test_fight_is_damage_removal():
    """格闘（fights target creature）も対象を取るダメージの除去（総合ルール 701.14a）"""
    es = entries("Prey Upon", "damage")
    assert len(es) == 1 and es[0]["targeted"] is True


@pytest.mark.parametrize("name", ["Lightning Bolt", "Anger of the Gods", "Unholy Heat",
                                  "Orcish Bowmasters", "Aether Flash"])
def test_damage_reaching_creatures_is_kept(name):
    assert "damage" in types(name)


@pytest.mark.parametrize("name", ["Adarkar Wastes", "Balin, Loremaster", "Mana Crypt",
                                  "Guttersnipe", "Juzám Djinn"])
def test_damage_to_players_only_is_not_removal(name):
    """プレイヤー・自分にしか届かないダメージは除去でない"""
    assert types(name) == []
    assert parse(CARDS[name])[3] is None


def test_anger_of_the_gods_is_not_targeted_and_bowmasters_is():
    assert entries("Anger of the Gods", "damage")[0]["targeted"] is False
    assert entries("Orcish Bowmasters", "damage")[0]["targeted"] is True


def test_kicker_extra_mode_is_kept():
    es = entries("Burst Lightning", "damage")
    assert [e["amount"] for e in es] == [2, 4]
    assert "extra_cost" in es[1]


@pytest.mark.parametrize("name", ["Galvanic Discharge", "Harnessed Lightning"])
def test_that_much_damage_to_a_chosen_permanent_is_removal(name):
    """数字が無い「deals that much damage to that creature / permanent」も、行き先が
    クリーチャー等ならダメージの除去（対象を取る）"""
    es = entries(name, "damage")
    assert len(es) == 1 and es[0]["targeted"] is True and es[0]["amount"] is None


def test_that_much_damage_to_any_target_is_kept():
    assert "damage" in types("Boros Reckoner")


@pytest.mark.parametrize("name", ["Eye for an Eye", "Repercussion", "Spiteful Sliver",
                                  "Mogg Maniac", "Jackal Pup", "Justice",
                                  "Pyromancer's Gauntlet"])
def test_that_much_damage_to_players_or_a_modifier_is_not_removal(name):
    """行き先がプレイヤー（that player / its controller / target player or planeswalker）の物と、
    ダメージ量を増やす置換（… that much damage plus 2 … instead）は除去でない"""
    assert "damage" not in types(name)


@pytest.mark.parametrize("name", ["Bilbo's Gambit"])
def test_bouncing_a_spell_is_not_removal(name):
    assert "bounce" not in types(name)


@pytest.mark.parametrize("name", ["Unsummon", "Vapor Snag", "Venser, Shaper Savant",
                                  "Hullbreaker Horror"])
def test_bouncing_permanents_stays_bounce(name):
    """パーマネントを戻すもの（spell or permanent・別のモードで permanent）は bounce のまま"""
    assert "bounce" in types(name)


@pytest.mark.parametrize("name", ["Ephemerate", "Flickerwisp", "Roll-Roll-Roll-Roll",
                                  "Restoration Angel", "Cloudshift", "Felidar Guardian"])
def test_blink_is_not_exile_removal(name):
    """自分の効果で戦場に戻る追放（ブリンク）は exile の項目を付けない"""
    assert "exile" not in types(name)


@pytest.mark.parametrize("name", ["Journey to Nowhere", "Banishing Light",
                                  "Swords to Plowshares"])
def test_permanent_exile_stays(name):
    es = entries(name, "exile")
    assert len(es) == 1 and es[0]["permanent"] is True


@pytest.mark.parametrize("name", ["Parting Gust", "Unyielding Gatekeeper", "Vindictive Triumph",
                                  "Koya, Death from Above"])
def test_conditional_return_stays_exile_removal(name):
    """戻すかどうかが if / unless / otherwise で決まる追放は、戦場に残らない道があるので除去のまま"""
    assert "exile" in types(name)


@pytest.mark.parametrize("name", ["Phelia, Exuberant Shepherd", "Eldrazi Confluence",
                                  "Hallowed Respite", "Gilraen, Dúnedain Protector"])
def test_unconditional_return_is_still_blink(name):
    assert "exile" not in types(name)


def test_exiling_own_tokens_is_not_removal():
    """自分のトークンを追放する文（you control）と、墓地のカードを追放する前半は除去でない"""
    assert types("Abyssal Harvester") == []


@pytest.mark.parametrize("name", ["Clutch of Undeath", "Gift of Fangs", "Nyx Infusion",
                                  "Serra's Boon", "Tahngarth's Rage", "Dead Weight"])
def test_aura_that_shrinks_the_enchanted_creature_is_minus(name):
    assert "minus" in types(name)


@pytest.mark.parametrize("name", ["Pacifism", "Arrest", "Claustrophobia",
                                  "Enchanted River's Grasp", "Kenrith's Transformation",
                                  "Darksteel Mutation", "Faith's Fetters", "Petrify"])
def test_pacify_auras(name):
    es = entries(name, "pacify")
    assert es == [{"type": "pacify", "targeted": True, "permanent": True}]


@pytest.mark.parametrize("name", ["Rancor", "Holy Strength", "Oppressive Rays"])
def test_buff_or_conditional_auras_are_not_pacify(name):
    assert "pacify" not in types(name)


@pytest.mark.parametrize("name", ["Moonshadow", "Devoted Druid", "Wall of Roots"])
def test_minus_counters_on_itself_are_not_removal(name):
    assert "minus" not in types(name)


@pytest.mark.parametrize("name", ["Toxic Deluge", "Glistening Deluge", "Dismember",
                                  "Skinrender", "Agony Warp", "Retribution", "Contagion"])
def test_minus_reaching_other_creatures_is_kept(name):
    assert "minus" in types(name)


def test_minus_amounts():
    assert entries("Dismember", "minus")[0]["amount"] == 5
    assert entries("Dismember", "minus")[0]["targeted"] is True
    assert entries("Toxic Deluge", "minus")[0]["amount"] == "X"


@pytest.mark.parametrize("name", ["Relic of Progenitus", "Bojuka Bog"])
def test_graveyard_exile_is_not_removal(name):
    assert "exile" not in types(name)


def test_farewell_still_exiles_creatures():
    assert "exile" in types("Farewell")


@pytest.mark.parametrize("name,expected", [
    ("Lightning Bolt", ["damage"]),
    ("Fatal Push", ["destroy"]),
    ("Swords to Plowshares", ["exile"]),
    ("Journey to Nowhere", ["exile"]),
    ("Wrath of God", ["destroy"]),
    ("Liliana of the Veil", ["sacrifice"]),
])
def test_existing_removal_anchors(name, expected):
    assert types(name) == expected


# ---------------------------------------------------------------- 条件の印

def entry(name, typ):
    es = entries(name, typ)
    assert len(es) >= 1, (name, typ)
    return es[0]


def test_mana_value_cap_and_revolt_upgrade():
    e = entry("Fatal Push", "destroy")
    assert e["mv_max"] == 2 and e["mv_max_upgrade"] == 4 and e["upgrade_by"] == "revolt"


def test_damage_upgrades():
    e = entry("Unholy Heat", "damage")
    assert (e["amount"], e["upgrade_amount"], e["upgrade_by"]) == (2, 6, "delirium")
    assert e["object"] == "creature_or_planeswalker"
    e = entry("Fiery Impulse", "damage")
    assert (e["amount"], e["upgrade_amount"], e["upgrade_by"]) == (2, 3, "spell mastery")
    es = entries("Burst Lightning", "damage")
    assert [x["amount"] for x in es] == [2, 4]  # 今の形（キッカーの追加項目）は壊さない
    assert (es[0]["upgrade_amount"], es[0]["upgrade_by"]) == (4, "kicker")
    assert "extra_cost" in es[1]


def test_requires_attacking_or_blocking():
    assert entry("Eiganjo, Seat of the Empire", "damage")["requires"] == ["attacking", "blocking"]


@pytest.mark.parametrize("name,typ", [("Devourer of Destiny", "exile"),
                                      ("Ugin, Eye of the Storms", "exile")])
def test_requires_colored(name, typ):
    assert entry(name, typ)["requires"] == ["colored"]


def test_requires_nonartifact_and_power_cap():
    assert entry("Go for the Throat", "destroy")["requires"] == ["nonartifact"]
    e = entry("Elektra, Daughter of the Hand", "destroy")
    assert e["power_max"] == 3 and "requires" not in e


@pytest.mark.parametrize("name,typ", [("Galvanic Discharge", "damage"),
                                      ("Prismatic Ending", "exile"),
                                      ("Rabid Bite", "damage"),
                                      ("Toxic Deluge", "minus")])
def test_variable_amount_or_cap(name, typ):
    assert entry(name, typ)["variable"] is True


def test_damage_object_values():
    assert entry("Lightning Bolt", "damage")["object"] == "any"
    assert entry("Anger of the Gods", "damage")["object"] == "creature"
    assert entry("Omnath, Locus of Creation", "damage")["object"] == "planeswalker"
    assert entry("Galvanic Discharge", "damage")["object"] == "creature_or_planeswalker"


def test_nonlegendary_polarity_and_pronoun_attacking():
    assert entry("Urza's Ruinous Blast", "exile")["requires"] == ["nonlegendary"]
    assert "requires" not in entry("Hellrider", "damage")  # "it's attacking" は対象の条件でない


@pytest.mark.parametrize("name,typ", [("Lightning Bolt", "damage"),
                                      ("Swords to Plowshares", "exile"),
                                      ("Journey to Nowhere", "exile")])
def test_plain_removal_has_no_condition_keys(name, typ):
    keys = set(entry(name, typ)) - {"type", "amount", "targeted", "object", "permanent"}
    assert keys == set()


@pytest.mark.parametrize("name", ["Aven Interrupter", "Spell Queller"])
def test_exiling_a_spell_is_not_removal(name):
    assert "exile" not in types(name)


@pytest.mark.parametrize("name", ["Mortician Beetle", "Mayhem Devil"])
def test_sacrifice_inside_a_trigger_condition_is_not_removal(name):
    assert "sacrifice" not in types(name)


# ---------------------------------------------------------------- ドロー

@pytest.mark.parametrize("name,count", [
    ("Brainstorm", 3),
    ("Faithless Looting", 2),
    ("Consider", 1),
    ("Mishra's Bauble", 1),
    ("Fear of Missing Out", 1),
    ("Hieroglyphic Illumination", 2),  # 本文の Draw two が注釈の Cycling より大きい
])
def test_existing_draw_anchors(name, count):
    assert parse_draw(CARDS[name]) == (count, None)


@pytest.mark.parametrize("name", ["Street Wraith", "Raffine's Informant", "Patient Instructor",
                                  "Great Gilded Boat"])
def test_keyword_only_draw_counts_as_one(name):
    """Cycling / Connive / Recruit は、draw が注釈文の中にしか無くても 1 枚"""
    assert parse_draw(CARDS[name]) == (1, None)


@pytest.mark.parametrize("name", ["Girder Goons", "Tenacious Underdog"])
def test_blitz_keyword_counts_as_one(name):
    """Blitz（注釈文に「死んだとき 1 枚引く」）を能力として持つ物は 1 枚"""
    assert parse_draw(CARDS[name]) == (1, None)


@pytest.mark.parametrize("name", ["Thoughtseize", "Troll of Khazad-dûm", "Tireless Tracker",
                                  "Fluctuator", "Glorious Purpose", "Recruit Instructor",
                                  "Henzie \"Toolbox\" Torre"])
def test_not_drawing_anchors(name):
    """型サイクリング・手掛かり・言及だけの文・カード名の一部は数えない"""
    assert parse_draw(CARDS[name]) == (None, None)


def test_variable_connive_is_draw_x():
    assert parse_draw(CARDS["Raffine, Scheming Seer"]) == (None, True)
