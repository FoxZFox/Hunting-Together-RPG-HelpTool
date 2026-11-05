"""Build simulator for the Build tab's auto build.

The game's stat formulas are Ragnarok Online's: measured on six bare Lv.99 classes on 2026-11-05/06
(data/stat_effects.json "formulas"). This file computes the Stats screen numbers for any stats, cards, Extra
passives and armour set, a damage-per-second figure from the class's skills, and picks the best of each.

Each class is anchored to its measured bare panel (all stats 1, nothing equipped), so constants the formulas
do not show (a level part of ATK, say) come out right. Only Lv.99 is modelled: the stat bonuses were read there.
Gear is not modelled piece by piece; what the worn gear adds can be given as plain numbers (GEAR_KEYS).
"""
import itertools
import json
import os
import re

from game import STAT_NAMES, stat_cost, stat_points_total

HERE = os.path.dirname(os.path.abspath(__file__))


def _data(name):
    with open(os.path.join(HERE, 'data', name), encoding='utf-8') as f:
        return json.load(f)


K_ATK, K_MATK = 1.026, 1.03          # left over in every class's ATK / MATK after the passives (measured)
DEF_CUT = 0.3                        # assumed share of damage monster DEF takes away (not measured)
FARM_TARGETS = 3                     # monsters an AoE skill hits when farming
EXTRA_SLOT_LEVELS = (70, 76, 82, 88, 94, 99)
# The Stats screen shows ASPD rounded DOWN (2026-11-06 01:53, Samurai AGI 94 + Berserk: 188 with Mirror Mind
# off, 189 with it on, and DEX +30 previewed only +0.1 because it ran into 190: the real value was 189.9, as the
# formula said). So a build at 189.9 shows 189. An ASPD target is met only when the formula clears it by this
# much, for the formula's own error (01:28: formula 178.9, screen 179 = real 179.0 or more).
ASPD_MARGIN = 0.5

# Per class, with only its own passives on. W: the ASPD gap factor without any attack speed %, so that
# W x (1 - own attack speed %) is what was measured. skills: (name, % of ATK or MATK, cooldown s or None for the
# one that follows ASPD, AoE, uses MATK). buff: what its own buffs keep up (their uptime is about 100% on Auto,
# Fade 8 s of every 20 s).
CLASSES = {
    'Samurai': dict(main='STR', atk_pct=.15, aspd_pct=.15, crit_k=.3, le_agi=.15, melee=True,
                    buff=dict(aspd_pct=.15, crit=15, crit_dmg=15),
                    skills=[('Quick Draw', 373, None, True, False), ('Blossom Dance', 430, 4, True, False),
                            ('Counter Stance', 910 * .5, 8, True, False)]),
    'Thief': dict(main='STR', atk_pct=.20, aspd_pct=.10, crit_k=.6, melee=True, buff=dict(dmg_mult=1 + .5 * 8 / 20),
                  skills=[('Cross Rend', 340, None, False, False), ('Venom Dash', 200, 7, False, False),
                          ('Umbral Strike', 500, 6, True, False)]),
    'Archer': dict(main='DEX', atk_pct=.20, aspd_pct=0, crit_k=.3, melee=False, dmg_pct=.10,
                   buff=dict(stat_pct={'AGI': .12, 'DEX': .12}),
                   skills=[('Echo Arrow', 400, None, False, False), ('Skyfall Volley', 150, 1.5, True, False),
                           ('Emerald Lance', 480, 6, True, False), ('Gale Step', 270, 5, False, False)]),
    'Merchant': dict(main='STR', atk_pct=.20, aspd_pct=0, crit_k=.3, melee=True, buff=dict(dmg_mult=1.10),
                     skills=[('Coin Smash', 320, None, False, False), ('Whirl Axe', 220, 1, True, False),
                             ('Earthsplitter', 300, 5, True, False)]),
    'Swordsman': dict(main='STR', atk_pct=.20, aspd_pct=.10, crit_k=.3, melee=True, buff=dict(dmg_mult=1.10),
                      skills=[('Crushing Blow', 270, None, False, False), ('Blazing Ring', 250, 3, True, False)]),
    'Cleric': dict(main='STR', atk_pct=.20, matk_pct=.20, aspd_pct=0, crit_k=.3, melee=True,
                   buff=dict(stats_flat=10, atk_pct=.10, matk_pct=.10, cdr=.10, cast=10),
                   skills=[('Attack', 100, None, False, False), ('Halo Burst', 585, 3, True, True),
                           ('Mending Light', 260, 3, False, True)]),
}
# the Cleric was measured with 5 Extra passives in its slots: take Hunter's Focus (DEX +10, ATK +20%) and
# Swift Step (attack speed +15%) back out
MEASURED_WITH = {'Cleric': dict(DEX=10, atk_pct=.20, aspd_pct=.15)}

# speed potions (Bag tooltips, 2026-11-06): only one at a time, a stronger one keeps a weaker one off
POTIONS = {
    'Concentration Potion': {'aspd_pct': .10, 'cast': 5, 'level': 1},
    'Awakening Potion': {'aspd_pct': .15, 'cast': 7.5, 'level': 40},
    'Berserk Potion': {'aspd_pct': .20, 'cast': 10, 'level': 85},
}
NO_POTION = 'ไม่กินยา'


def _cast_curve():
    """Cast Speed % against total DEX, one curve for every class (Samurai and Archer sweeps)."""
    se = _data('stat_effects.json')
    pts = {}
    for job in ('Samurai', 'Archer'):
        c = se['classes'][job]
        t0 = 1 + c['bonus']['DEX']
        pts[t0] = c['base_panel']['Cast Speed']
        for v, d in zip(se['at'], c['sweeps']['DEX']['Cast Speed']):
            pts[v + c['bonus']['DEX']] = c['base_panel']['Cast Speed'] + d
    xs = sorted(pts)
    return xs, [pts[x] for x in xs]


CAST_X, CAST_Y = _cast_curve()


def cast_from_dex(dex):
    import numpy as np
    return float(np.interp(dex, CAST_X, CAST_Y))


# buffs that show in the Stats screen while up (read off the buff bar when the screen is read), from the
# skills' own wording; Grace, Sacred Aegis and Battle Rally also come from party members
BUFF_EFFECTS = {
    'Mirror Mind': {'aspd_pct': .15, 'crit': 15, 'crit_dmg': 15},
    'Forest Pulse': {'stat_pct': {'AGI': .12, 'DEX': .12}},
    'Grace': {'STR': 10, 'AGI': 10, 'INT': 10, 'DEX': 10, 'atk_pct': .10, 'matk_pct': .10, 'cast': 10},
    'Sacred Aegis': {},
    'Battle Rally': {},
}

# ASPD_NOTE: attack speed % sources multiply: each shrinks the gap to 200 by (1 - its %). Adding them up
# missed the user's Samurai by 4 ASPD (2026-11-06: 5 sources, 179 seen, 183 added up, 178.9 multiplied; the AGI
# previews +3.8 / +0.6 matched too). The class W keeps the class's own sources inside.

GEAR_KEYS = ('atk', 'atk_pct', 'aspd', 'aspd_pct', 'crit', 'crit_dmg', 'def_pen', 'dmg_pct',
             'STR', 'AGI', 'VIT', 'INT', 'DEX', 'LUK')


# ---- effects: one dict of numbers per card, Extra passive or set ----------------------------------------
def _add(e, key, v):
    e[key] = e.get(key, 0) + v


def parse_effect(text, job=None):
    """Numbers out of the game's own wording ('ATK +20%', 'STR +10', 'Crit Rate +3%', 'Attack Speed +8%' ...)."""
    e = {}
    t = text.replace('−', '-')
    for m in re.finditer(r'\b(STR|AGI|VIT|INT|DEX|LUK)\s*\+(\d+)(?!\s*%)', t):
        _add(e, m.group(1), int(m.group(2)))
    for m in re.finditer(r'(?<![MD])\bATK\s*\+\s*(\d+)(%?)', t):
        _add(e, 'atk_pct' if m.group(2) else 'atk', int(m.group(1)) / (100 if m.group(2) else 1))
    for m in re.finditer(r'\bMATK\s*\+\s*(\d+)(%?)', t):
        _add(e, 'matk_pct' if m.group(2) else 'matk', int(m.group(1)) / (100 if m.group(2) else 1))
    if re.search(r'ATK and MATK \+(\d+)%', t):
        v = int(re.search(r'ATK and MATK \+(\d+)%', t).group(1)) / 100
        _add(e, 'atk_pct', v)
        _add(e, 'matk_pct', v)
    for m in re.finditer(r'(?:Crit Rate|CRIT)\s*\+(\d+)', t):
        _add(e, 'crit', int(m.group(1)))
    for m in re.finditer(r'Crit DMG\s*\+(\d+)%', t, re.I):
        _add(e, 'crit_dmg', int(m.group(1)))
    for m in re.finditer(r'[Aa]ttack [Ss]peed\s*\+(\d+)%', t):
        _add(e, 'aspd_pct', int(m.group(1)) / 100)
    for m in re.finditer(r'\bASPD\s*\+(\d+)(?!%)', t):
        _add(e, 'aspd', int(m.group(1)))
    for m in re.finditer(r'(?<!M)DEF Pen\s*\+?(\d+)%', t):
        _add(e, 'def_pen', int(m.group(1)) / 100)
    for m in re.finditer(r'(?:Skill )?Cooldown\s*-(\d+)%', t):
        _add(e, 'cdr', int(m.group(1)) / 100)
    for m in re.finditer(r'Cast Speed\s*\+(\d+(?:\.\d+)?)%', t):
        _add(e, 'cast', float(m.group(1)))
    for m in re.finditer(r'[Dd]amage dealt \+(\d+)%', t):
        _add(e, 'dmg_pct', int(m.group(1)) / 100)
    m = re.search(r'ATK \+ (\d+)% of your AGI', t)
    if m:
        e.pop('atk', None)
        _add(e, 'le_agi', int(m.group(1)) / 100)
    if re.search(r'Bow: .*damage \+(\d+)%', t):
        e = {'dmg_pct': int(re.search(r'damage \+(\d+)%', t).group(1)) / 100, 'bow': True}
    if re.search(r'double-strike on normal attacks', t):
        e = {'double_hit': .5, 'melee': True}
    if re.search(r'[Mm]elee weapon', t):
        e['melee'] = True
    return e


def card_effects(card, job):
    e = parse_effect(f"{card.get('stats', '')}, {card.get('ability', '')}")
    boost = card.get('class_boost', '')
    m = re.match(r'(\w+): (.+)', boost)
    if m and m.group(1) == job:
        for name, *_ in CLASSES[job]['skills']:
            hit = re.search(rf'{re.escape(name)}(?: damage)? \+(\d+)%', m.group(2))
            if hit:
                e.setdefault('skill_dmg', {})[name] = int(hit.group(1)) / 100
    return e


# ---- the Stats screen -------------------------------------------------------------------------------------
class Model:
    """One class at Lv.99: its measured bare panel plus the formulas."""

    def __init__(self, job):
        se = _data('stat_effects.json')['classes'][job]
        self.job, self.c = job, CLASSES[job]
        less = MEASURED_WITH.get(job, {})
        self.bonus = {s: se['bonus'][s] - less.get(s, 0) for s in STAT_NAMES}
        p = se['base_panel']
        self.atk_pct0 = self.c['atk_pct']
        self.minor = 'STR' if self.c['main'] == 'DEX' else 'DEX'
        bare = dict.fromkeys(STAT_NAMES, 1)
        # what the panel showed, less the Extras it was measured with
        seen = self._totals(bare, {'DEX': less.get('DEX', 0)})
        self.atk_x = p['ATK'] / ((1 + self.atk_pct0 + less.get('atk_pct', 0)) * K_ATK) - self._atk_raw(seen, {})
        self.matk_x = p['MATK'] / ((1 + self.c.get('matk_pct', 0)) * K_MATK) - self._matk_raw(self._totals(bare, {}))
        gap = 200 - p['ASPD']
        t = self._totals(bare, {'DEX': less.get('DEX', 0)})
        own = 1 - (1 - self.c['aspd_pct']) * (1 - less.get('aspd_pct', 0))
        self.W = gap / (1 - own) / (1 - t['AGI'] / 250 - t['DEX'] / 1000)
        self.crit0 = p['Crit Rate'] - self.c['crit_k'] * self._totals(bare, {})['LUK']
        self.cast0 = p['Cast Speed'] - cast_from_dex(seen['DEX'])
        self.crit_dmg0 = p['Crit DMG']
        sw = se['sweeps']['VIT']['Max HP']
        self.hp_per_vit = sw[-1] / 98
        self.hp0 = p['Max HP'] - self.hp_per_vit * (1 + self.bonus['VIT'])

    def _totals(self, stats, extra):
        return {s: stats[s] + self.bonus[s] + extra.get(s, 0) for s in STAT_NAMES}

    def _atk_raw(self, t, e):
        m = t[self.c['main']]
        raw = m + (m // 10) ** 2 + t[self.minor] // 5 + t['LUK'] // 5
        return raw + (self.c.get('le_agi', 0) + e.get('le_agi', 0)) * t['AGI'] + e.get('atk', 0)

    @staticmethod
    def _matk_raw(t):
        return t['INT'] + (t['INT'] // 5) ** 2

    def panel(self, stats, e):
        """Stats screen numbers for these stats with the summed effects e (cards, Extras, set, gear, buffs)."""
        extra = {s: e.get(s, 0) for s in STAT_NAMES}
        t = self._totals(stats, extra)
        for s, pct in e.get('stat_pct', {}).items():
            t[s] = int(t[s] * (1 + pct))
        atk = (self._atk_raw(t, e) + self.atk_x) * (1 + self.atk_pct0 + e.get('atk_pct', 0)) * K_ATK
        matk = (self._matk_raw(t) + self.matk_x + e.get('matk', 0)) * (1 + self.c.get('matk_pct', 0)
                                                                         + e.get('matk_pct', 0)) * K_MATK
        keep = (1 - self.c['aspd_pct']) * (1 - e.get('aspd_pct', 0))
        aspd = 200 - self.W * keep * (1 - t['AGI'] / 250 - t['DEX'] / 1000) + e.get('aspd', 0)
        crit = self.crit0 + self.c['crit_k'] * t['LUK'] + e.get('crit', 0)
        cast = self.cast0 + cast_from_dex(t['DEX']) + e.get('cast', 0)
        return {'ATK': round(atk), 'MATK': round(matk), 'ASPD': round(min(190.0, aspd), 1),
                'ASPD uncapped': round(aspd, 1), 'Crit Rate': round(min(100.0, crit), 1),
                'Crit DMG': self.crit_dmg0 + e.get('crit_dmg', 0), 'Cast Speed': round(cast, 1),
                'Max HP': round((self.hp0 + self.hp_per_vit * t['VIT']) * (1 + e.get('hp_pct', 0))),
                'stats': t}

    def dps(self, stats, e, farm=False):
        """Damage per second on one target (boss) or with FARM_TARGETS around for AoE skills. Skills come first
        whenever they are ready (the user, 2026-11-06); each use takes one attack's time. Relative figure."""
        pn = self.panel(stats, e)
        hits = 50 / (200 - pn['ASPD'])
        crit = pn['Crit Rate'] / 100
        crit_mult = 1 + crit * pn['Crit DMG'] / 100
        cdr = min(.6, e.get('cdr', 0))
        uses, total = 0.0, 0.0
        aspd_skill = None
        for name, pct, cd, aoe, magic in self.c['skills']:
            if cd is None:
                aspd_skill = (name, pct, aoe, magic)
                continue
            per = 1 / (cd * (1 - cdr))
            per = min(per, max(0.0, hits - uses))
            uses += per
            total += per * self._hit(name, pct, aoe, magic, pn, crit, crit_mult, e, farm)
        name, pct, aoe, magic = aspd_skill
        total += max(0.0, hits - uses) * self._hit(name, pct, aoe, magic, pn, crit, crit_mult, e, farm)
        pen = min(1.0, e.get('def_pen', 0))
        return total * (1 + self.c.get('dmg_pct', 0) + e.get('dmg_pct', 0)) * e.get('dmg_mult', 1) \
            * (1 - DEF_CUT * (1 - pen))

    def _hit(self, name, pct, aoe, magic, pn, crit, crit_mult, e, farm):
        boost = 1 + e.get('skill_dmg', {}).get(name, 0)
        if magic:                                     # magic does not crit (only Alchemist flasks can)
            dmg = pn['MATK'] * pct / 100 * boost
        else:
            dmg = pn['ATK'] * pct / 100 * boost * crit_mult
            if name == 'Venom Dash':                  # on crit it dashes again, up to 6 times
                dmg *= 1 + sum(crit ** i for i in range(1, 7))
            if name == 'Attack' and e.get('double_hit') and self.c['melee']:
                dmg *= 1 + e['double_hit']
        return dmg * (FARM_TARGETS if aoe and farm else 1)


def combine(*effects):
    out = {}
    for e in effects:
        for k, v in e.items():
            if k in ('melee', 'bow'):
                continue
            if isinstance(v, dict):
                d = out.setdefault(k, {})
                for kk, vv in v.items():
                    d[kk] = d.get(kk, 0) + vv
            elif k == 'dmg_mult':
                out[k] = out.get(k, 1) * v
            elif k == 'aspd_pct':                     # each source shrinks the gap on its own (see ASPD_NOTE)
                out[k] = 1 - (1 - out.get(k, 0)) * (1 - v)
            else:
                out[k] = out.get(k, 0) + v
    return out


def class_buffs(job):
    b = dict(CLASSES[job]['buff'])
    flat = b.pop('stats_flat', 0)
    for s in ('STR', 'AGI', 'INT', 'DEX'):
        if flat:
            b[s] = b.get(s, 0) + flat
    return b


def set_effects(s):
    return parse_effect(s.get('bonus5', ''))


# ---- choosing -------------------------------------------------------------------------------------------
def all_cards(job):
    """[(name, type, effects)] for every card in the Card Book."""
    out = []
    for a in _data('cards.json')['areas']:
        for c in a['cards']:
            out.append((c['monster'], c['type'], card_effects(c, job)))
    return out


def all_extras(job, unlocked=None):
    """Extra passives this class may slot: another class's, not class-only, and usable with its weapon.
    unlocked: classes at Lv.99 (None = all)."""
    melee = CLASSES[job]['melee']
    out = []
    for x in _data('extras.json')['extras']:
        if x['class'] == job or x.get('class_only'):
            continue
        if unlocked is not None and x['class'] not in unlocked:
            continue
        e = parse_effect(x['effect'])
        if e.get('bow') and job != 'Archer':
            continue
        if e.get('melee') and not melee:
            continue
        out.append((x['name'], x['class'], e))
    return out


def all_sets(easy_only=False):
    out = []
    for s in _data('sets.json')['sets']:
        if easy_only and s.get('obtain') != 'easy':
            continue
        out.append((s['name'], s.get('obtain'), set_effects(s)))
    return out


def best_stats(model, e, points, aspd_min=0.0, crit_min=0.0, farm=False, start=None, fixed=None, e_target=None):
    """Spend points on the six stats: first whatever reaches the ASPD and Crit wanted most cheaply, then the
    most damage per point. Looks ahead to the next step of 10 (main stat) or 5 (the /5 stats), where the
    floor() in the formulas pays out. The ASPD target gets ASPD_MARGIN on top. e_target: the effects the
    targets must hold with (e.g. without the class's timed buffs); damage is still worked out with e."""
    et = e if e_target is None else e_target
    cur = dict(start or dict.fromkeys(STAT_NAMES, 1))
    cur.update(fixed or {})
    left = points - sum(stat_cost(v) for s in STAT_NAMES for v in range(1, cur[s]))
    free = [s for s in STAT_NAMES if s not in (fixed or {})]

    def steps(s):
        if s not in free:
            return []
        out = []
        for n in (1, 2, 3, 4, 5, 10):
            if cur[s] + n > 99:
                break
            out.append(n)
        nxt = (cur[s] // 5 + 1) * 5 - cur[s]
        if 0 < nxt <= 10 and cur[s] + nxt <= 99:
            out.append(nxt)
        return sorted(set(out))

    def cost(s, n):
        return sum(stat_cost(v) for v in range(cur[s], cur[s] + n))

    aspd_want = aspd_min and aspd_min + ASPD_MARGIN
    for want, key, stats_for in ((aspd_want, 'ASPD uncapped', ('AGI', 'DEX')), (crit_min, 'Crit Rate', ('LUK',))):
        while want and model.panel(cur, et)[key] < want:
            base = model.panel(cur, et)[key]
            opts = []
            for s in stats_for:
                for n in steps(s):
                    c = cost(s, n)
                    if c <= left:
                        gain = model.panel(dict(cur, **{s: cur[s] + n}), et)[key] - base
                        if gain > 0:
                            opts.append((gain / c, s, n, c))
            if not opts:
                break
            _, s, n, c = max(opts)
            cur[s] += n
            left -= c
    while True:
        here = model.dps(cur, e, farm)
        opts = []
        for s in STAT_NAMES:
            for n in steps(s):
                c = cost(s, n)
                if c <= left:
                    gain = model.dps(dict(cur, **{s: cur[s] + n}), e, farm) - here
                    if gain > 0:
                        opts.append((gain / c, s, n, c))
        if not opts:
            break
        _, s, n, c = max(opts)
        cur[s] += n
        left -= c
    return cur, left


def best_build(job, owned=None, unlocked=None, easy_sets=False, aspd_min=0.0, crit_min=0.0, farm=False,
               gear=None, slots=6, card_slots=8, rounds=3, potion='Berserk Potion', aspd_idle=False):
    """Stats, 8 cards, the Extra slots and an armour set together, a few rounds of each in turn.
    owned: card names this account has (None = every card). aspd_idle: the ASPD and Crit targets must hold
    without the class's timed buffs (Mirror Mind and the like), as on the Stats screen out of a fight."""
    model = Model(job)
    gear = {k: v for k, v in (gear or {}).items() if k in GEAR_KEYS and v}
    if 'atk_pct' in gear:
        gear['atk_pct'] /= 100
    if 'aspd_pct' in gear:
        gear['aspd_pct'] /= 100
    if 'def_pen' in gear:
        gear['def_pen'] /= 100
    if 'dmg_pct' in gear:
        gear['dmg_pct'] /= 100
    pot = {k: v for k, v in POTIONS.get(potion, {}).items() if k != 'level'}
    base = combine(class_buffs(job), gear, pot)
    idle = combine(gear, pot)                     # the same without the class's timed buffs

    def target(*parts):                           # what the ASPD / Crit targets are checked with
        return combine(idle, *parts) if aspd_idle else None
    cards = [c for c in all_cards(job) if owned is None or c[0] in owned]
    extras = all_extras(job, unlocked)
    sets = all_sets(easy_sets) + [('ไม่ใส่ชุดเซ็ท', None, {})]
    points = stat_points_total(99)
    pick_c, pick_x, pick_s = [], [], sets[-1]
    stats = None
    for _ in range(rounds):
        e = combine(base, pick_s[2], *(c[2] for c in pick_c), *(x[2] for x in pick_x))
        stats, _ = best_stats(model, e, points, aspd_min, crit_min, farm,
                              e_target=target(pick_s[2], *(c[2] for c in pick_c), *(x[2] for x in pick_x)))
        # set
        def with_(cs, xs, st):
            return combine(base, st[2], *(c[2] for c in cs), *(x[2] for x in xs))
        pick_s = max(sets, key=lambda st: model.dps(stats, with_(pick_c, pick_x, st), farm))
        # cards, one slot at a time
        pick_c = []
        for _ in range(card_slots):
            rest = [c for c in cards if c not in pick_c]
            if not rest:
                break
            here = model.dps(stats, with_(pick_c, pick_x, pick_s), farm)
            best = max(rest, key=lambda c: model.dps(stats, with_(pick_c + [c], pick_x, pick_s), farm))
            if model.dps(stats, with_(pick_c + [best], pick_x, pick_s), farm) <= here:
                break
            pick_c.append(best)
        # Extras
        pick_x = []
        for _ in range(slots):
            rest = [x for x in extras if x not in pick_x]
            if not rest:
                break
            here = model.dps(stats, with_(pick_c, pick_x, pick_s), farm)
            best = max(rest, key=lambda x: model.dps(stats, with_(pick_c, pick_x + [x], pick_s), farm))
            if model.dps(stats, with_(pick_c, pick_x + [best], pick_s), farm) <= here:
                break
            pick_x.append(best)
    e = combine(base, pick_s[2], *(c[2] for c in pick_c), *(x[2] for x in pick_x))
    e_screen = combine(idle, pick_s[2], *(c[2] for c in pick_c), *(x[2] for x in pick_x))
    stats, left = best_stats(model, e, points, aspd_min, crit_min, farm, e_target=e_screen if aspd_idle else None)
    pn = model.panel(stats, e)
    ps = model.panel(stats, e_screen)
    notes = []
    # each set with the rest kept and the stats chosen again for it
    ranking = []
    for st in sets:
        es = combine(base, st[2], *(c[2] for c in pick_c), *(x[2] for x in pick_x))
        ss, _ = best_stats(model, es, points, aspd_min, crit_min, farm,
                           e_target=target(st[2], *(c[2] for c in pick_c), *(x[2] for x in pick_x)))
        ranking.append({'name': st[0], 'obtain': st[1], 'dps': round(model.dps(ss, es, farm))})
    ranking.sort(key=lambda r: -r['dps'])
    top = ranking[0]['dps'] or 1
    for r in ranking:
        r['pct'] = round(100 * r['dps'] / top, 1)
    # Crit against ATK: LUK held at a few values, everything else chosen again
    luk = []
    for v in (1, 25, 50, 75, 99):
        sl, left_l = best_stats(model, e, points, aspd_min, 0, farm, fixed={'LUK': v},
                                e_target=e_screen if aspd_idle else None)
        if left_l < 0:
            continue
        pl = model.panel(sl, e)
        luk.append({'LUK': v, 'stats': sl, 'ATK': pl['ATK'], 'Crit Rate': pl['Crit Rate'],
                    'ASPD': pl['ASPD'], 'dps': round(model.dps(sl, e, farm))})
    best_luk = max((r['dps'] for r in luk), default=1) or 1
    for r in luk:
        r['pct'] = round(100 * r['dps'] / best_luk, 1)
    held = ps if aspd_idle else pn                # the panel the targets were asked of
    when = ' (ตอนไม่มีบัฟอาชีพ)' if aspd_idle else ''
    if aspd_min and held['ASPD uncapped'] < aspd_min:
        notes.append(f'ASPD ถึงแค่ {held["ASPD"]}{when} ไม่ถึง {aspd_min:g} ที่ตั้งไว้')
    elif aspd_min and held['ASPD uncapped'] < aspd_min + ASPD_MARGIN:
        notes.append(f'ASPD ตามสูตรได้ {held["ASPD uncapped"]:.1f}{when} เผื่อไม่ถึง {ASPD_MARGIN:g} '
                     f'(สูตรคลาดได้ราว 1) ในเกมอาจขึ้น {aspd_min - 1:g} แทน {aspd_min:g}')
    if crit_min and held['Crit Rate'] < crit_min:
        notes.append(f'Crit ถึงแค่ {held["Crit Rate"]}%{when} ไม่ถึง {crit_min:g}% ที่ตั้งไว้')
    return {
        'job': job, 'stats': stats, 'points_left': left, 'notes': notes,
        'cards': [{'name': c[0], 'type': c[1]} for c in pick_c],
        'extras': [{'name': x[0], 'class': x[1]} for x in pick_x],
        'set': {'name': pick_s[0], 'obtain': pick_s[1]},
        'panel': {k: v for k, v in pn.items() if k != 'stats'},
        'hits_per_s': round(50 / (200 - pn['ASPD']), 2),
        'dps': round(model.dps(stats, e, farm)),
        'potion': potion if pot else NO_POTION,
        'panel_screen': {k: ps[k] for k in ('ATK', 'ASPD', 'ASPD uncapped', 'Crit Rate', 'Crit DMG')},
        'aspd_idle': bool(aspd_idle),
        'set_ranking': ranking, 'luk_compare': luk,
    }


def extra_slots(level):
    return sum(level >= lv for lv in EXTRA_SLOT_LEVELS)


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    for job in CLASSES:
        r = best_build(job)
        print(job, r['stats'], r['set']['name'], [c['name'] for c in r['cards']], [x['name'] for x in r['extras']],
              r['panel'], r['dps'])


def gear_from_reading(job, rd, equipped=None):
    """What the worn gear adds, from a Stats screen reading (Game.read_stats_screen): the reading less the
    bare class and less the cards, Extras and set worn then (equipped: {'set': name, 'cards': [...],
    'extras': [...]}). ATK% and attack speed % come from the +10 previews; without them (no free points)
    the gear is taken to add none. Returns (gear for best_build, notes)."""
    m = Model(job)
    equipped = dict(equipped or {})
    if not equipped.get('extras') and rd.get('extras'):
        equipped['extras'] = [x for x in rd['extras'] if x]       # what the Skills tab showed slotted
    cards = {c[0]: c[2] for c in all_cards(job)}
    extras = {x['name']: parse_effect(x['effect']) for x in _data('extras.json')['extras']}
    sets = {s['name']: set_effects(s) for s in _data('sets.json')['sets']}
    # buffs and the potion up at the time (read off the buff bar) count in the screen too: take them out
    up = [{k: v for k, v in (BUFF_EFFECTS.get(b) or POTIONS.get(b) or {}).items() if k != 'level'}
          for b in rd.get('buffs') or []]
    pct = combine(*up).get('stat_pct', {})
    eq = combine(sets.get(equipped.get('set'), {}), *(cards[n] for n in equipped.get('cards', []) if n in cards),
                 *(extras[n] for n in equipped.get('extras', []) if n in extras),
                 *({k: v for k, v in u.items() if k != 'stat_pct'} for u in up))
    stats, bonus, pan, sp = rd['stats'], rd['bonus'], rd['panel'], rd.get('special', {})
    notes = [f"หักบัฟที่ติดอยู่ตอนอ่านออกแล้ว: {', '.join(rd['buffs'])}"] if rd.get('buffs') else []
    gear = {}
    t = {s: int(stats[s] + (bonus.get(s) or 0)) for s in STAT_NAMES}             # as the screen worked it out
    for s in STAT_NAMES:
        before_pct = round(t[s] / (1 + pct.get(s, 0)))                           # Forest Pulse adds a % on top
        gear[s] = int(before_pct - stats[s] - m.bonus[s] - eq.get(s, 0))
    main = m.c['main']
    pv = rd.get('previews', {})
    p_atk = m.atk_pct0 + eq.get('atk_pct', 0)
    if pv.get(main, {}).get('ATK'):
        grown = round(t[main] / (1 + pct.get(main, 0))) + pv[main]['add']
        t2 = dict(t, **{main: int(grown * (1 + pct.get(main, 0)))})
        p_atk = pv[main]['ATK'] / (m._atk_raw(t2, {}) - m._atk_raw(t, {})) / K_ATK - 1
    else:
        notes.append('ไม่มีแต้มว่างให้ลองกด + เลยถือว่าชุดไม่ได้ให้ ATK% (ATK ที่เกินมานับเป็น ATK ตัวเลข)')
    if pan.get('ATK'):
        flat = pan['ATK'] / ((1 + p_atk) * K_ATK) - (m._atk_raw(t, {}) + m.atk_x)
        gear['atk'] = round(flat - eq.get('atk', 0))
    gear['atk_pct'] = round(100 * (p_atk - m.atk_pct0 - eq.get('atk_pct', 0)), 1)
    agi = pv.get('AGI', {})
    keep = (1 - m.c['aspd_pct']) * (1 - eq.get('aspd_pct', 0))       # what is known to shrink the gap
    seen_keep = keep
    shown = pan.get('ASPD')
    if shown and shown >= 190:
        notes.append('ASPD ตอนอ่านตันที่ 190 แล้ว บอกไม่ได้ว่าชุดให้ความเร็วเกินมาเท่าไหร่')
    elif agi.get('ASPD') and shown + agi['ASPD'] > 189:
        notes.append('ลองกด AGI แล้ว ASPD ชน 190 เลยใช้หาว่าความเร็วเพิ่มกี่ % ไม่ได้')
    elif agi.get('ASPD'):
        grown = int((round(t['AGI'] / (1 + pct.get('AGI', 0))) + agi['add']) * (1 + pct.get('AGI', 0)))
        seen_keep = agi['ASPD'] * 250 / ((grown - t['AGI']) * m.W)          # AGI the preview really added
    else:
        notes.append('ไม่มีแต้มว่างให้ลองกด AGI เลยถือว่าชุดไม่ได้ให้ Attack Speed %')
    if shown and shown < 190:                         # at the cap the flat part cannot be told either
        def gap(tt):
            return m.W * seen_keep * (1 - tt['AGI'] / 250 - tt['DEX'] / 1000)
        real = shown + 0.5                            # the screen rounds down: somewhere in [shown, shown + 1)
        for s in ('AGI', 'DEX'):                      # a + preview that stopped at 190 tells it exactly
            d = pv.get(s, {}).get('ASPD')
            if d is None:
                continue
            grown = int((round(t[s] / (1 + pct.get(s, 0))) + pv[s]['add']) * (1 + pct.get(s, 0)))
            if gap(t) - gap(dict(t, **{s: grown})) > d + 0.15 and shown + d > 189:
                real = 190 - d
                notes.append(f'ASPD จริงราว {real:.1f} (หน้า Stats ปัดเศษลงเป็น {shown:g}) '
                             f'ดูจากที่ลองกด {s} แล้วขึ้นแค่ +{d:g} เพราะชน 190')
                break
        gear['aspd'] = round(real - (200 - gap(t)) - eq.get('aspd', 0), 1)
    seen_pct = round(100 * (1 - seen_keep / keep), 1)
    gear['aspd_pct'] = 0.0                            # gear cannot give attack speed % (data/gear.json)
    if pan.get('Crit Rate') is not None:
        gear['crit'] = round(pan['Crit Rate'] - (m.crit0 + m.c['crit_k'] * t['LUK']) - eq.get('crit', 0), 1)
    if sp.get('Crit DMG') is not None:
        gear['crit_dmg'] = round(sp['Crit DMG'] - m.crit_dmg0 - eq.get('crit_dmg', 0))
    if sp.get('DEF Pen') is not None:
        gear['def_pen'] = round(sp['DEF Pen'] - 100 * eq.get('def_pen', 0))
    if equipped.get('extras'):
        notes.append(f"หัก EX ที่ใส่อยู่ออกแล้ว: {', '.join(equipped['extras'])}")
    # gear cannot give these (data/gear.json): what shows is cards or Extras not taken out
    if seen_pct > 1.5:
        notes.append(f"ชุดให้ Attack Speed % ไม่ได้ (แหวนให้ ASPD เป็นตัวเลขเท่านั้น) ที่เห็น +{seen_pct}% "
                     'น่าจะมาจากการ์ดหรือ EX ที่ใส่อยู่แต่ยังไม่ได้เลือก')
    if gear.get('atk_pct', 0) > 12:
        notes.append(f"ชุดให้ ATK% ได้ราว 10% เท่านั้น (Special ของอาวุธ) ที่เห็น +{gear['atk_pct']}% "
                     'น่าจะมาจากการ์ดหรือ EX ที่ใส่อยู่แต่ยังไม่ได้เลือก')
    neg = [k for k in ('atk', 'atk_pct', 'crit', 'crit_dmg') if gear.get(k, 0) < -2]
    if neg:
        notes.append('ค่าติดลบ (' + ', '.join(neg) + ') แปลว่าการ์ด EX ชุดเซ็ทที่เลือกว่าใส่อยู่ไม่ตรงกับในเกม '
                     'หรือมียา/บัฟค้างตอนอ่าน')
    return gear, notes


def gear_from_worn(job, rd, equipped=None):
    """What the worn gear adds, from the pieces read one by one in the Bag (Game.worn_gear): their options
    added up, nothing guessed. The Stats screen read with them (Extras taken out) is then worked out from
    those numbers and compared, line by line, so a mismatch shows instead of hiding in the gear.
    equipped: {'cards': [...]} for cards worn (the bot only counts them). Returns (gear, notes, check)."""
    worn = rd.get('worn') or {}
    gear = {k: v for k, v in (worn.get('gear') or {}).items() if k in GEAR_KEYS}
    notes = []
    m = Model(job)
    set_name, pieces = (worn.get('set') or [None, 0])[:2]
    sets = {s['name']: set_effects(s) for s in _data('sets.json')['sets']}
    set_e = sets.get(set_name, {}) if pieces >= 5 else {}
    if set_name and pieces < 5:
        notes.append(f'ใส่ {set_name} แค่ {pieces}/5 ชิ้น โบนัสเซ็ทยังไม่ติด')
    cards = {c[0]: c[2] for c in all_cards(job)}
    picked = [n for n in (equipped or {}).get('cards', []) if n in cards]
    n_cards = worn.get('cards_worn')
    if n_cards and len(picked) < n_cards:
        notes.append(f'ใส่การ์ดอยู่ {n_cards} ใบ แต่เลือกไว้ {len(picked)} ใบ เลือกการ์ดที่ใส่ด้วย (บอทยังอ่านชื่อการ์ดไม่ได้)')
    empty = [p['slot'] for p in worn.get('pieces', []) if p.get('empty')]
    if empty:
        notes.append('ช่องที่ไม่มีของหรืออ่านไม่ได้: ' + ', '.join(empty))
    up = [{k: v for k, v in (BUFF_EFFECTS.get(b) or POTIONS.get(b) or {}).items() if k != 'level'}
          for b in rd.get('buffs') or []]
    if rd.get('buffs'):
        notes.append('ตอนอ่านมีบัฟติดอยู่ (' + ', '.join(rd['buffs']) + ') ครั้งหน้าอย่ากดบัฟก่อนอ่าน')
    g = {k: (v / 100 if k in ('atk_pct', 'aspd_pct', 'def_pen', 'dmg_pct') else v) for k, v in gear.items()}
    e = combine(g, set_e, *(cards[n] for n in picked), *up)
    check = []
    stats = rd.get('stats') or {}
    if stats and all(stats.get(s) for s in STAT_NAMES):
        pn = m.panel(stats, e)
        for row, key, tol in (('ATK', 'ATK', 2), ('ASPD', 'ASPD uncapped', 1), ('Crit Rate', 'Crit Rate', 1)):
            seen = (rd.get('panel') or {}).get(row)
            if seen is None:
                continue
            want = pn[key]
            ok = (seen <= want < seen + 1) if row == 'ASPD' else abs(want - seen) <= tol   # ASPD shows rounded down
            check.append({'row': row, 'screen': seen, 'items': round(want, 1), 'ok': bool(ok)})
        cd = (rd.get('special') or {}).get('Crit DMG')
        if cd is not None:
            want = m.crit_dmg0 + e.get('crit_dmg', 0)
            check.append({'row': 'Crit DMG', 'screen': cd, 'items': want, 'ok': abs(cd - want) <= 1})
        for s in STAT_NAMES:
            seen = (rd.get('bonus') or {}).get(s)
            if seen is None:
                continue
            want = m.bonus[s] + e.get(s, 0)
            check.append({'row': f'{s} bonus', 'screen': seen, 'items': want, 'ok': abs(seen - want) < 0.5})
        pv = (rd.get('previews') or {}).get(m.c['main'])
        if pv and pv.get('ATK'):
            t = m._totals(stats, {s: e.get(s, 0) for s in STAT_NAMES})
            main = m.c['main']
            t2 = dict(t, **{main: t[main] + pv['add']})
            raw = m._atk_raw(t2, {}) - m._atk_raw(t, {})
            seen_pct = round(100 * (pv['ATK'] / raw / K_ATK - 1 - m.atk_pct0), 1)
            want_pct = round(100 * e.get('atk_pct', 0), 1)
            check.append({'row': f'ATK% (ลองกด {main} +{pv["add"]})', 'screen': seen_pct, 'items': want_pct,
                          'ok': abs(seen_pct - want_pct) <= 1.5})
        bad = [c for c in check if not c['ok']]
        if bad:
            notes.append('ไม่ตรงกับหน้า Stats: ' + ', '.join(f"{c['row']} เกม {c['screen']:g} / คิดจากของ {c['items']:g}"
                                                              for c in bad))
    return gear, notes, check
