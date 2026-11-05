"""Read Status & Skills > Stats by glyph templates: the right-hand panel (each row's value and the green
preview delta that pending + presses add) and the bonus beside each stat. Windows OCR drops short numbers
and decimal points there, so the digits are matched against templates/stats_panel/, cut from screenshots
whose numbers were checked by eye (2026-11-05/06, six Lv.99 classes).

Positions were measured with the window at stats_dx = -66 and move with it (Game.stats_dx).
Values are dark 12 px glyphs (green when an Extra passive or buff changes them), deltas green 9 px ones,
the stat bonuses blue (green when an Extra adds to them) ~8 px ones.
"""
import os
import re

import cv2
import numpy as np

STAT_NAMES = ('STR', 'AGI', 'VIT', 'INT', 'DEX', 'LUK')
ROWS = ('Max HP', 'Max SP', 'ATK', 'MATK', 'DEF', 'MDEF', 'Evasion', 'Cast Speed', 'ASPD', 'Crit Rate', 'HP Regen')
ROW_Y = (146, 185, 225, 264, 304, 344, 383, 423, 462, 502, 542)
STAT_Y = (220, 289, 358, 426, 495, 563)
SPECIAL = (('Crit DMG', 603, 'L'), ('DEF Pen', 603, 'R'), ('MDEF Pen', 631, 'L'), ('DMG Reduction', 631, 'R'),
           ('Cooldown', 659, 'L'), ('Move Speed', 659, 'R'), ('SP Regen', 687, 'L'), ('Lifesteal', 687, 'R'))
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, 'templates', 'stats_panel')


def _masks(crop):
    c = crop.astype(int)
    b, g, r = c[:, :, 0], c[:, :, 1], c[:, :, 2]
    dark = c.max(axis=2) < 125
    green = (g > 100) & (g - r > 30) & (g - b > 30)
    blue = (b > 120) & (b - r > 60) & (b - g > 20)
    return dark, green, blue


def ink(crop, kind):
    """Binary ink for one kind of number. A value can be green: green pieces as tall as a value digit,
    and what touches them, count as value; smaller green pieces are deltas."""
    dark, green, blue = _masks(crop)
    if kind == 'bonus':
        return ((blue | green).astype(np.uint8)) * 255
    if kind == 'special':                 # green when above 0, grey "0%" otherwise
        c = crop.astype(int)
        grey = (c.max(axis=2) < 175) & (c.max(axis=2) - c.min(axis=2) < 30)
        return ((green | grey).astype(np.uint8)) * 255
    g8 = green.astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(g8, connectivity=8)
    # tell value from delta by how far a piece reaches above the baseline (12 px against 9): a comma
    # hangs below it and would make a delta piece as tall as a value digit
    bottoms = [st[i][1] + st[i][3] for i in range(1, n) if st[i][3] >= 8]
    base = min(bottoms) if bottoms else 0             # the digits' baseline: a comma only goes below it
    tall = [i for i in range(1, n) if base - st[i][1] >= 11]
    keep = np.zeros(n, bool)
    for i in range(1, n):
        x, w = st[i][0], st[i][2]
        keep[i] = i in tall or any(x <= st[j][0] + st[j][2] + 3 and st[j][0] <= x + w + 3 for j in tall)
    gval = keep[lab] & green
    on = (dark | gval) if kind == 'value' else (green & ~gval)
    return on.astype(np.uint8) * 255


def strip(img, key, dx, kind):
    """The binary strip holding one number: a panel row (value / delta) or a stat's bonus."""
    if kind == 'bonus':
        y = STAT_Y[STAT_NAMES.index(key)]
        x0 = 495 + 66 + dx
        return ink(img[y - 12:y + 12, x0:x0 + 50], kind)
    if kind == 'special':
        name, y, side = next(s for s in SPECIAL if s[0] == key)
        x0 = (790 if side == 'L' else 945) + 66 + dx
        return ink(img[y - 11:y + 11, x0:x0 + 55], kind)
    y = ROW_Y[ROWS.index(key)]
    x0 = 845 + 66 + dx
    return ink(img[y - 13:y + 13, x0:x0 + 152], kind)


def pieces(mask):
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return sorted((int(s[0]), int(s[1]), int(s[2]), int(s[3])) for s in st[1:] if s[4] >= 2)


def load_templates():
    out = {'value': [], 'delta': [], 'bonus': []}
    if not os.path.isdir(TEMPLATE_DIR):
        return out
    for f in sorted(os.listdir(TEMPLATE_DIR)):
        m = re.fullmatch(r'(value|delta|bonus)_(.+)_\d+\.png', f)
        if m:
            ch = {'plus': '+', 'pct': '%'}.get(m.group(2), m.group(2))
            out[m.group(1)].append((ch, cv2.imread(os.path.join(TEMPLATE_DIR, f), cv2.IMREAD_GRAYSCALE)))
    out['bonus'] += out['delta']          # the same 9 px font, only blue instead of green
    return out


def _plus(mask):
    """A leading '+' (deltas and bonuses always start with one; it often touches the first digit):
    its columns, or None. Its left two columns hold only the 2-row bar."""
    cols = np.where(mask.any(axis=0))[0]
    if len(cols) < 8:
        return None
    x = int(cols[0])
    left = np.where(mask[:, x:x + 2].any(axis=1))[0]
    mid = np.where(mask[:, x + 2:x + 4].any(axis=1))[0]
    if len(left) and left.max() - left.min() <= 2 and len(mid) and mid.max() - mid.min() >= 4:
        return x, x + 6
    return None


def read_text(mask, templates, thr=0.62):
    """Best non-overlapping template matches left to right, plus decimal dots."""
    plus = _plus(mask)
    if plus:
        mask = mask.copy()
        mask[:, plus[0]:plus[1]] = 0
    cands = []
    for ch, t in templates:
        if t.shape[0] > mask.shape[0] or t.shape[1] > mask.shape[1]:
            continue
        res = cv2.matchTemplate(mask, t, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= thr)
        for y, x in zip(ys, xs):
            cands.append((float(res[y, x]), int(x) + 1, int(x) + t.shape[1] - 1, ch))
    taken = []
    for score, x0, x1, ch in sorted(cands, reverse=True):
        if all(x1 <= a + 1 or x0 >= b - 1 for _, a, b, _ in taken):
            taken.append((score, x0, x1, ch))
    big = [p for p in pieces(mask) if p[3] > 4]
    base = max((p[1] + p[3] for p in big), default=0)
    # '.': a run of 2-3 columns inked only in the bottom 3 rows, between two digits (it can touch the
    # digit before it, as in "+2.3")
    top = mask.argmax(axis=0)
    low = mask.any(axis=0) & (top >= base - 3)
    digits = [x0 for _, x0, _, ch in taken if ch.isdigit()]
    x = 0
    while x < len(low):
        if low[x]:
            e = x
            while e + 1 < len(low) and low[e + 1]:
                e += 1
            if 2 <= e - x + 1 <= 3 and any(d < x for d in digits) and any(d > e for d in digits):
                taken.append((1.0, x, e + 1, '.'))
            x = e + 1
        else:
            x += 1
    if plus:
        taken.append((1.0, plus[0], plus[1], '+'))
    return ''.join(ch for _, x0, _, ch in sorted(taken, key=lambda t: t[1]))


def number(text):
    """'+1.247' is a thousands comma read as a dot (decimals here have one digit)."""
    t = text.replace('%', '').replace('+', '')
    if t.count('.') == 1 and len(t.split('.')[1]) == 3:
        t = t.replace('.', '')
    try:
        return float(t) if t else None
    except ValueError:
        return None


class Reader:
    def __init__(self):
        self.t = load_templates()

    def ready(self):
        return all(self.t.values())

    def panel(self, img, dx):
        """{row: (value, delta)}: value None when unreadable, delta 0.0 when nothing is pending."""
        return {row: (number(read_text(strip(img, row, dx, 'value'), self.t['value'])),
                      number(read_text(strip(img, row, dx, 'delta'), self.t['delta'])) or 0.0)
                for row in ROWS}

    def special(self, img, dx):
        """{name: value} for the Special Stats box (Crit DMG, DEF Pen, Cooldown ...), in %."""
        return {name: number(read_text(strip(img, name, dx, 'special'), self.t['delta'])) for name, _, _ in SPECIAL}

    def bonus(self, img, dx):
        """{stat: the bonus beside it} (cards, gear, Extras and passives that add to the stat)."""
        return {s: number(read_text(strip(img, s, dx, 'bonus'), self.t['bonus'])) for s in STAT_NAMES}


# ---- the buff bar above HP on the plain HUD ------------------------------------------------------
BUFF_DIR = os.path.join(HERE, 'templates', 'buffs')
BUFF_RECT = (430, 555, 370, 60)           # x, y, w, h: where the buff and potion icons sit
POTION_NAMES = ('Concentration Potion', 'Awakening Potion', 'Berserk Potion')


def load_buffs():
    out = {}
    if os.path.isdir(BUFF_DIR):
        for f in sorted(os.listdir(BUFF_DIR)):
            if f.endswith('.png'):
                im = cv2.imread(os.path.join(BUFF_DIR, f))
                name = f[:-4].replace('_', ' ')
                # skill icons come from the Skills tab, where a slot-number badge covers the top-left corner
                out[name] = im if name in POTION_NAMES else im[20:, 20:]
    return out


def buffs(img, templates=None):
    """Names of the class buffs and the speed potion showing in the buff bar. Skill icons score 0.85-0.96
    there when up and 0.6 or less when not (2026-11-06); the three potion bottles look alike, so only the
    best of them counts, and only from 0.85."""
    templates = templates if templates is not None else load_buffs()
    x, y, w, h = BUFF_RECT
    roi = img[y:y + h, x:x + w]
    score = {}
    for name, t0 in templates.items():
        best = 0.0
        for s in np.arange(0.40, 0.70, 0.02):
            t = cv2.resize(t0, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            if t.shape[0] <= roi.shape[0] and t.shape[1] <= roi.shape[1]:
                best = max(best, cv2.minMaxLoc(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED))[1])
        score[name] = best
    found = [n for n, v in score.items() if n not in POTION_NAMES and v >= 0.75]
    pots = sorted((v, n) for n, v in score.items() if n in POTION_NAMES)
    if pots and pots[-1][0] >= 0.85:
        found.append(pots[-1][1])
    return found
