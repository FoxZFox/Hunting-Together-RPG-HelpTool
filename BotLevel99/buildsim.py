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
                   buff=dict(stats_flat=10, atk_pct=.10, matk_pct=.10, cdr=.10),
                   skills=[('Attack', 100, None, False, False), ('Halo Burst', 585, 3, True, True),
                           ('Mending Light', 260, 3, False, True)]),
}
# the Cleric was measured with 5 Extra passives in its slots: take Hunter's Focus (DEX +10, ATK +20%) and
# Swift Step (attack speed +15%) back out
MEASURED_WITH = {'Cleric': dict(DEX=10, atk_pct=.20, aspd_pct=.15)}

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
        own = self.c['aspd_pct'] + less.get('aspd_pct', 0)
        self.W = gap / (1 - own) / (1 - t['AGI'] / 250 - t['DEX'] / 1000)
        self.crit0 = p['Crit Rate'] - self.c['crit_k'] * self._totals(bare, {})['LUK']
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
        p = self.c['aspd_pct'] + e.get('aspd_pct', 0)
        aspd = 200 - self.W * max(0.0, 1 - p) * (1 - t['AGI'] / 250 - t['DEX'] / 1000) + e.get('aspd', 0)
        crit = self.crit0 + self.c['crit_k'] * t['LUK'] + e.get('crit', 0)
        return {'ATK': round(atk), 'MATK': round(matk), 'ASPD': round(min(190.0, aspd), 1),
                'ASPD uncapped': round(aspd, 1), 'Crit Rate': round(min(100.0, crit), 1),
                'Crit DMG': self.crit_dmg0 + e.get('crit_dmg', 0),
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


def best_stats(model, e, points, aspd_min=0.0, crit_min=0.0, farm=False, start=None):
    """Spend points on the six stats: first whatever reaches the ASPD and Crit wanted most cheaply, then the
    most damage per point. Looks ahead to the next step of 10 (main stat) or 5 (the /5 stats), where the
    floor() in the formulas pays out."""
    cur = dict(start or dict.fromkeys(STAT_NAMES, 1))
    left = points - sum(stat_cost(v) for s in STAT_NAMES for v in range(1, cur[s]))

    def steps(s):
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

    for want, key, stats_for in ((aspd_min, 'ASPD uncapped', ('AGI', 'DEX')), (crit_min, 'Crit Rate', ('LUK',))):
        while want and model.panel(cur, e)[key] < want:
            base = model.panel(cur, e)[key]
            opts = []
            for s in stats_for:
                for n in steps(s):
                    c = cost(s, n)
                    if c <= left:
                        gain = model.panel(dict(cur, **{s: cur[s] + n}), e)[key] - base
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
               gear=None, slots=6, card_slots=8, rounds=3):
    """Stats, 8 cards, the Extra slots and an armour set together, a few rounds of each in turn.
    owned: card names this account has (None = every card)."""
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
    base = combine(class_buffs(job), gear)
    cards = [c for c in all_cards(job) if owned is None or c[0] in owned]
    extras = all_extras(job, unlocked)
    sets = all_sets(easy_sets) + [('ไม่ใส่ชุดเซ็ท', None, {})]
    points = stat_points_total(99)
    pick_c, pick_x, pick_s = [], [], sets[-1]
    stats = None
    for _ in range(rounds):
        e = combine(base, pick_s[2], *(c[2] for c in pick_c), *(x[2] for x in pick_x))
        stats, _ = best_stats(model, e, points, aspd_min, crit_min, farm)
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
    stats, left = best_stats(model, e, points, aspd_min, crit_min, farm)
    pn = model.panel(stats, e)
    notes = []
    if aspd_min and pn['ASPD uncapped'] < aspd_min:
        notes.append(f'ASPD ถึงแค่ {pn["ASPD"]} ไม่ถึง {aspd_min:g} ที่ตั้งไว้')
    if crit_min and pn['Crit Rate'] < crit_min:
        notes.append(f'Crit ถึงแค่ {pn["Crit Rate"]}% ไม่ถึง {crit_min:g}% ที่ตั้งไว้')
    return {
        'job': job, 'stats': stats, 'points_left': left, 'notes': notes,
        'cards': [{'name': c[0], 'type': c[1]} for c in pick_c],
        'extras': [{'name': x[0], 'class': x[1]} for x in pick_x],
        'set': {'name': pick_s[0], 'obtain': pick_s[1]},
        'panel': {k: v for k, v in pn.items() if k != 'stats'},
        'hits_per_s': round(50 / (200 - pn['ASPD']), 2),
        'dps': round(model.dps(stats, e, farm)),
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
