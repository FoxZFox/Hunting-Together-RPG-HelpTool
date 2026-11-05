"""Reading the worn gear one piece at a time, for the Build tab.

In the Bag, tapping a worn slot opens a dark popup beside it that lists the piece's options:

    SlotWeapon · Tier V
    ATK +226                    base stat(s), then "Refine +4: ATK +15" on an upgraded piece
    Special Options
    ATK +7%
    Random Stats
    STR +10 / DEX +6 / LUK +5
    Warrior Set (5/5) · Common  (set pieces only)
    5-piece set: ATK +10%
    Lv. 80+
    [Unequip]                   never pressed; neither is the small red X on the slot

The popup is see-through (the character shows behind it), and Windows OCR drops short lines such as
"DEF +8". Each line is therefore cut out on its own, cleaned to dark text on white, and sent three times
side by side: OCR keeps a longer line, and the three copies vote (2026-11-06, all 8 of the user's
Samurai pieces read right this way; one line at a time lost a third of them).
"""
import difflib
import json
import os
import re
from collections import Counter

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, 'templates', 'bag')

# The Bag's equipment panel at 1280x720. Tap points sit at the lower left of each icon, away from the
# red X the game puts on the top right of the open slot (that X takes the piece off).
SLOTS = (('Upper Head', (70, 212)), ('Weapon', (70, 294)), ('Cloak', (70, 375)), ('Acc.1', (70, 456)),
         ('Mid Head', (398, 212)), ('Body', (398, 294)), ('Boots', (398, 375)), ('Acc.2', (398, 456)))
PANEL_W = 500                      # the popup always opens over the left-hand equipment panel
CARD_SLOTS = [(109 + 44.3 * i, 560) for i in range(8)]      # "Cards 0 / 8" row under the set line

# every option name the game lists (data/gear.json) plus what else shows on a piece
_GEAR = json.load(open(os.path.join(HERE, 'data', 'gear.json'), encoding='utf-8'))
VOCAB = sorted({o['stat'] for s in _GEAR['slots'].values() for sec in ('special', 'random')
                for o in s.get(sec, [])} | {'ATK', 'MATK', 'DEF', 'MDEF', 'HP', 'SP', 'SP Regen', 'HP Regen',
                                            'Potion Heal', 'Evasion', 'Move Speed', 'Lifesteal', 'Cooldown'})
VOCAB = [v for v in VOCAB if '+' not in v and '(' not in v]
ALIASES = {'OF': 'DEF', 'DEE': 'DEF', 'W': 'HP', 'H P': 'HP', 'Cit': 'Crit Rate', 'Crit': 'Crit Rate',
           'SIR': 'STR', 'AM': 'ATK', 'Mow Speed': 'Move Speed', 'Mow': 'Move Speed'}

# option name -> the key buildsim's best_build takes (gear values in its units: % as whole numbers)
TO_GEAR = {'ATK': 'atk', 'ATK%': 'atk_pct', 'ASPD': 'aspd', 'Crit Rate': 'crit', 'Crit Rate%': 'crit',
           'Crit DMG%': 'crit_dmg', 'DEF Pen%': 'def_pen', 'STR': 'STR', 'AGI': 'AGI', 'VIT': 'VIT',
           'INT': 'INT', 'DEX': 'DEX', 'LUK': 'LUK'}


# ---- finding the popup and its lines --------------------------------------------------------------
def popup_box(img):
    """(x, y, w, h) of the item popup, or None. It is the one flat dark-grey box about 290 px wide over
    the equipment panel; the box test leaves out the panel's dark outline and the character."""
    panel = img[:, :PANEL_W].astype(int)
    mx, mn = panel.max(axis=2), panel.min(axis=2)
    bg = ((mx >= 14) & (mx <= 36) & (mx - mn <= 12)).astype(np.uint8)
    bg = cv2.morphologyEx(bg, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    bg = cv2.morphologyEx(bg, cv2.MORPH_OPEN, np.ones((21, 21), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(bg, connectivity=4)
    found = []
    for i in range(1, n):
        if st[i, cv2.CC_STAT_WIDTH] < 230 or st[i, cv2.CC_STAT_HEIGHT] <= 120:
            continue
        x, y, w, h = (int(v) for v in st[i, :4])
        # the longest run of columns the box fills top to bottom: what joins it at one end (Roblox's dark
        # menu buttons when the popup opens high up, 2026-11-06 Cloak) fills only a few rows of them
        cols = list((lab[y:y + h, x:x + w] == i).mean(axis=0) > 0.6) + [False]
        best, run = (0, 0), 0
        for j, c in enumerate(cols):
            if c:
                run += 1
                continue
            if run > best[1]:
                best = (j - run, run)
            run = 0
        if 230 <= best[1] <= 340:
            found.append((int(st[i, cv2.CC_STAT_AREA]), x + best[0], y, best[1], h))
    if not found:
        return None
    _, x, y, w, h = max(found)
    bright = (mx[y:y + h, x:x + w] > 150).mean(axis=0)           # the white panel beside the popup
    while w > 0 and bright[0] > 0.4:
        x, w, bright = x + 1, w - 1, bright[1:]
    while w > 0 and bright[-1] > 0.4:
        w, bright = w - 1, bright[:-1]
    return x, y, w, h


def _is_button(line):
    """The blue Unequip bar (the popup's options end there)."""
    b, g, r = (line[:, :, k].astype(int) for k in range(3))
    return ((b > 200) & (r < 90) & (g > 100) & (g < 200)).mean() > 0.35


def line_spans(crop):
    """(top, bottom) of each text line: rows with bright pixels (white, grey, orange or green text; what
    shows through the popup stays darker)."""
    on = (crop[:, 8:-8].max(axis=2) > 140).sum(axis=1) > 2
    out, y = [], 0
    while y < len(on):
        if on[y]:
            s = y
            while y < len(on) and on[y]:
                y += 1
            if y - s >= 6:
                out.append((s, y))
        y += 1
    return out


INK = 150                          # text is white 255, grey 178 or orange/green at full strength


def line_masks(img, box=None, strict=False):
    """Binary ink (0/255) of each text line of the popup, top to bottom, up to the Unequip button.
    strict: only the cores of the strokes, where glyphs that touch come apart."""
    box = box or popup_box(img)
    if not box:
        return []
    x, y, w, h = box
    crop = img[y:y + h, x:x + w]
    out = []
    for s, e in line_spans(crop):
        line = crop[max(0, s - 1):e + 1, 4:-4]
        if _is_button(line):
            break
        out.append(_drop_edges(line_ink(line, strict)))
    return out


def _drop_edges(mask):
    """Clear a bar of ink down the left or right edge of a line (the white panel next to the popup):
    text never fills a column from top to bottom."""
    full = (mask > 0).mean(axis=0) > 0.75
    for rng in (range(0, min(6, mask.shape[1])), range(mask.shape[1] - 1, max(-1, mask.shape[1] - 7), -1)):
        for x in rng:
            if full[x]:
                mask[:, x] = 0
            elif not mask[:, x].any():
                continue
            else:
                break
    return mask


def line_ink(line, strict=False):
    """0/255 ink of one line. Headers, the Refine line and "Lv." are grey (178) rather than white: their
    strokes need a lower cut, which the dimmed character behind the popup stays under."""
    m = line.max(axis=2)
    top = np.percentile(m[m > 120], 90) if (m > 120).sum() > 10 else 255
    cut = (225 if top > 210 else 170) if strict else (INK if top > 210 else 128)
    return ((m > cut).astype(np.uint8)) * 255


def glyph_runs(mask):
    """Column runs with ink, left to right: one per glyph in this font (letters touch now and then)."""
    cols = list(mask.any(axis=0)) + [False]
    out, start = [], None
    for x, c in enumerate(cols):
        if c and start is None:
            start = x
        elif not c and start is not None:
            out.append((start, x))
            start = None
    return out


def _plus_shape(g):
    """A '+' in this font: 6-7 px wide, a 2-row bar across the middle and a 2-col bar down it, 5-6 rows
    tall, starting 2 rows below the cap height."""
    if not 5 <= g.shape[1] <= 8:
        return False
    rows = np.where(g.any(axis=1))[0]
    if not len(rows) or not 4 <= rows.max() - rows.min() + 1 <= 7:
        return False
    edge = np.where(g[:, :2].any(axis=1))[0]
    mid = np.where(g[:, g.shape[1] // 2 - 1:g.shape[1] // 2 + 1].any(axis=1))[0]
    return len(edge) and edge.max() - edge.min() <= 2 and len(mid) and mid.max() - mid.min() >= 4


def find_plus(mask, glyphs=None):
    """Column spans of the '+' signs on a line: by shape (each is its own column run in this font, or
    the left part of one when the digit after it touches), or else by the '+' templates (the grey
    Refine line draws it thinner)."""
    out = []
    for a, b in glyph_runs(mask):
        for w in (6, 7):
            if b - a >= w and _plus_shape(mask[:, a:a + w]):
                if b - a == w or not mask[:, a + w - 1:a + w + 1].all(axis=0).any():
                    out.append((a, a + w))
                    break
    if out or not glyphs:
        return out
    region = cv2.copyMakeBorder(mask, 2, 2, 0, 0, cv2.BORDER_CONSTANT, value=0)
    hits = []
    for ch, t in glyphs:
        if ch != '+':
            continue
        for x in range(0, region.shape[1] - t.shape[1] + 1):
            v = _iou(region, t, x)
            if v >= 0.6:
                hits.append((v, x, x + t.shape[1]))
    for v, a, b in sorted(hits, reverse=True):
        if all(b <= c or a >= d for c, d in out):
            out.append((a, b))
    return sorted(out)


POPUP_DIR = os.path.join(HERE, 'templates', 'gear_popup')


def load_popup_templates():
    """{'glyphs': [(char, img)], 'names': [(name, img)], 'marks': {key: img}} from templates/gear_popup."""
    out = {'glyphs': [], 'names': [], 'marks': {}}
    if not os.path.isdir(POPUP_DIR):
        return out
    for f in sorted(os.listdir(POPUP_DIR)):
        im = cv2.imread(os.path.join(POPUP_DIR, f), cv2.IMREAD_GRAYSCALE)
        m = re.fullmatch(r'glyph_(.+)_\d+\.png', f)
        if m:
            out['glyphs'].append(({'plus': '+', 'pct': '%', 'dot': '.'}.get(m.group(1), m.group(1)), im))
        elif f.startswith('name_'):
            out['names'].append((f[5:-4], im))
        else:
            out['marks'][f[:-4]] = im
    return out


def _iou(region, t, x, dy_max=2):
    """Best overlap (intersection over union of ink) of template t placed at column x of region,
    over a couple of rows up or down."""
    th, tw = t.shape
    tb = t > 0
    best = 0.0
    for dy in range(0, max(1, region.shape[0] - th + 1)):
        if abs(dy - (region.shape[0] - th) // 2) > dy_max:
            continue
        win = region[dy:dy + th, x:x + tw] > 0
        if win.shape != tb.shape:
            continue
        u = (win | tb).sum()
        if u:
            best = max(best, (win & tb).sum() / u)
    return best


def read_number(region, glyphs, thr=0.72):
    """Digits left to right from the start of region (ink after a '+'), then '%' if ink follows them.
    (text, x where the number ended)."""
    region = cv2.copyMakeBorder(region, 2, 2, 0, 8, cv2.BORDER_CONSTANT, value=0)
    inked = region.any(axis=0)
    cands = []
    for ch, t in glyphs:
        if not (ch.isdigit() or ch == '%'):
            continue
        tw = t.shape[1]
        for x in range(0, region.shape[1] - tw + 1):
            if not inked[x:x + tw].any():
                continue
            v = _iou(region, t, x)
            if v >= thr:
                cands.append((v, x, x + tw, ch))
    taken = []
    for sc, a, b, ch in sorted(cands, reverse=True):
        if all(b <= c + 1 or a >= d - 1 for _, c, d, _ in taken):
            taken.append((sc, a, b, ch))
    taken.sort(key=lambda t: t[1])
    text, end = '', 0
    for sc, a, b, ch in taken:                      # one run of digits from the left, gaps of 3 px at most
        if (text and a > end + 3) or (not text and a > 4) or (ch == '%' and not text):
            break
        text, end = text + ch, b
        if ch == '%':
            break
    return text, end


def _best_name(region, names, min_score=0.7):
    """The option name whose template fits this region best. The template must be about as wide as the
    region's ink, so "SP" does not pass for "SP Regen"."""
    cols = np.where(region.any(axis=0))[0]
    if not len(cols):
        return None
    region = cv2.copyMakeBorder(region[:, cols[0]:cols[-1] + 1], 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=0)
    best = (0.0, None)
    for name, t in names:
        if abs(t.shape[1] + 4 - region.shape[1]) > 3:
            continue
        v = max((_iou(region, t, x) for x in range(0, region.shape[1] - t.shape[1] + 1)), default=0.0)
        if v > best[0]:
            best = (v, name)
    return best[1] if best[0] >= min_score else None


def read_option(mask, tpl):
    """One option line by templates: ('name', value, is_percent, refine level or None), or None when it
    is no option line or its name is not known yet."""
    pluses = find_plus(mask, tpl['glyphs'])
    if not pluses:
        return None
    p0, p1 = pluses[-1]
    m2 = mask.copy()
    m2[:, p0:p1 + 1] = 0
    text, _ = read_number(m2[:, p1:], tpl['glyphs'])
    if not text or not text[0].isdigit():
        return None
    value, pct = float(text.rstrip('%')), text.endswith('%')
    name_part = mask[:, :p0]
    refine = None
    if len(pluses) >= 2:                               # "Refine +4: ATK +15"
        q0, q1 = pluses[0]
        r2 = mask.copy()
        r2[:, q0:q1 + 1] = 0
        lvl, end = read_number(r2[:, q1:p0], tpl['glyphs'])
        refine = int(lvl.rstrip('%')) if lvl[:1].isdigit() else None
        after = mask[:, q1 + end:p0]
        runs = glyph_runs(after)
        if runs and runs[0][1] - runs[0][0] <= 3:      # the colon
            after = after[:, runs[0][1]:]
        name_part = after
    name = _best_name(name_part, tpl['names'])
    return (name, value, pct, refine) if name else None


def _clean(line):
    m = line.max(axis=2).astype(np.float32)
    return 255 - np.clip((m - 100) * 255 / 155, 0, 255).astype(np.uint8)


def popup_lines(img, box=None):
    """[(colour line, ink mask)] of the popup, top to bottom, up to the Unequip button."""
    box = box or popup_box(img)
    if not box:
        return []
    x, y, w, h = box
    crop = img[y:y + h, x:x + w]
    out = []
    for s, e in line_spans(crop):
        line = crop[max(0, s - 1):e + 1, 4:-4]
        if _is_button(line):
            break
        out.append((line, _drop_edges(line_ink(line))))
    return out


def ocr_line(ocr, line):
    """One line by Windows OCR, sent three times side by side (it drops a short line sent alone)."""
    g = _clean(line)
    sep = np.full((g.shape[0], 30), 255, np.uint8)
    im = cv2.copyMakeBorder(np.hstack([g, sep, g, sep, g]), 10, 10, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return ' '.join(l.text for l in ocr.read(cv2.cvtColor(im, cv2.COLOR_GRAY2BGR), None, 4.0, 'color'))


def _green(line):
    """The set bonus line ("5-piece set: ATK +10%") is drawn green."""
    c = line.astype(int)
    g = (c[:, :, 1] > 150) & (c[:, :, 1] - c[:, :, 2] > 50) & (c[:, :, 1] - c[:, :, 0] > 40)
    return g.sum() > 25


def _header(mask, tpl):
    """'special' or 'random' for the two grey section headers, by their templates."""
    cols = np.where(mask.any(axis=0))[0]
    if not len(cols):
        return None
    region = cv2.copyMakeBorder(mask[:, cols[0]:cols[-1] + 1], 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=0)
    for key, name in (('head_special', 'special'), ('head_random', 'random')):
        t = tpl['marks'].get(key)
        if t is None or abs(t.shape[1] + 4 - region.shape[1]) > 4:
            continue
        if max((_iou(region, t, x) for x in range(0, region.shape[1] - t.shape[1] + 1)), default=0) > 0.7:
            return name
    return None


# ---- what the lines say -----------------------------------------------------------------------------
_OPT = re.compile(r"([A-Za-z][A-Za-z .']*?)\s*\+\s*(\d+(?:[.,]\d+)?)\s*(%?)")
_SET = re.compile(r"([A-Za-z][A-Za-z ]*?)\s*Set\s*[({\[]\s*(\d)\s*/\s*(\d)", re.I)
_TIER = re.compile(r"Tier\s*([IVX]{1,4})")


def stat_name(raw, cutoff=0.75):
    raw = re.sub(r'\s+', ' ', raw.strip(" .:'|"))
    raw = re.sub(r'^[Il1|] ', '', raw)                 # a stray edge read as "I"
    if raw in ALIASES:
        return ALIASES[raw]
    hit = (difflib.get_close_matches(raw, VOCAB, 1, cutoff) or difflib.get_close_matches(raw.upper(), VOCAB, 1, cutoff)
           or difflib.get_close_matches(raw.title(), VOCAB, 1, cutoff))
    return hit[0] if hit else None


def _learn_name(mask, name):
    """Keep the name part of a line OCR read the same three times, so it reads by template next time."""
    path = os.path.join(POPUP_DIR, f'name_{name}.png')
    pl = find_plus(mask)
    if os.path.exists(path) or not pl:
        return
    part = mask[:, :pl[-1][0]]
    cols = np.where(part.any(axis=0))[0]
    if len(cols):
        os.makedirs(POPUP_DIR, exist_ok=True)
        cv2.imwrite(path, part[:, cols[0]:cols[-1] + 1])


def read_popup(ocr, img, tpl, slot=None):
    """One worn piece from its open popup: {'slot', 'tier', 'base', 'special', 'random', 'refine', 'set',
    'unread'}. Each option is (name, value, is_percent). Options read by template (gear_popup/), and by
    OCR where the name has no template yet; headers by template, the tier and set lines by OCR."""
    item = {'slot': slot, 'tier': None, 'base': [], 'refine': [], 'special': [], 'random': [], 'set': None,
            'unread': []}
    section, started = 'base', False                # started: past the item's name (the "Slot ... Tier" line)
    for line, mask in popup_lines(img):
        if _green(line):
            continue                                   # the set bonus: data/sets.json has it
        head = _header(mask, tpl)
        if head:
            section = head
            continue
        opt = read_option(mask, tpl)
        if opt:
            started = True
            name, value, pct, refine = opt
            (item['refine'] if refine is not None else item[section]).append((name, value, pct))
            continue
        text = ocr_line(ocr, line) if ocr else ''
        low = text.lower()
        if 'tier' in low:
            started = True
            t = _TIER.search(text)
            item['tier'] = t.group(1) if t else item['tier']
            continue
        m = _SET.search(text)
        if m:
            item['set'] = (m.group(1).strip().title() + ' Set', int(m.group(2)), int(m.group(3)))
            continue
        if re.search(r'lv\s*\.?\s*\d', low) or not find_plus(mask, tpl['glyphs']):
            continue                                   # level needed, the item name, the icon
        votes = Counter((stat_name(n), v.replace(',', '.'), p) for n, v, p in _OPT.findall(text))
        best = [(k, c) for k, c in votes.most_common() if k[0]] if started else []
        if not best:
            if started:
                item['unread'].append(text)
            continue
        (name, val, p), agree = best[0]
        if agree >= 3:
            _learn_name(mask, name)
        item[section].append((name, float(val), p == '%'))
    return item


def gear_totals(items):
    """All worn pieces added up: (gear for best_build, other options for show). The set bonus is left
    out (buildsim adds the set itself)."""
    gear, other = {}, {}
    for it in items:
        for sec in ('base', 'refine', 'special', 'random'):
            for name, val, pct in it.get(sec, []):
                key = TO_GEAR.get(name + ('%' if pct else '')) or (TO_GEAR.get(name) if not pct else None)
                if key:
                    gear[key] = round(gear.get(key, 0) + val, 1)
                else:
                    label = name + (' %' if pct else '')
                    other[label] = round(other.get(label, 0) + val, 1)
    return gear, other


def worn_set(items):
    """(set name as in data/sets.json, pieces worn) of the set most pieces belong to, or (None, 0)."""
    sets = Counter(it['set'][0] for it in items if it.get('set'))
    if not sets:
        return None, 0
    name, n = sets.most_common(1)[0]
    known = [s['name'] for s in json.load(open(os.path.join(HERE, 'data', 'sets.json'), encoding='utf-8'))['sets']]
    hit = difflib.get_close_matches(name, known, 1, 0.6) or difflib.get_close_matches(name.replace(' Set', ''), known, 1, 0.6)
    return (hit[0] if hit else name), n


# ---- cards and Extra slots: empty or not ----------------------------------------------------------
def _template(name):
    path = os.path.join(TEMPLATE_DIR, name)
    return cv2.imread(path) if os.path.exists(path) else None


def cards_worn(img):
    """How many of the 8 card slots under the set line hold a card (Bag open), or None without the
    empty-slot template."""
    t = _template('card_empty.png')
    if t is None:
        return None
    th, tw = t.shape[:2]
    n = 0
    for x, y in CARD_SLOTS:
        x, y = int(x), int(y)
        roi = img[y - th // 2 - 6:y + th // 2 + 6, x - tw // 2 - 6:x + tw // 2 + 6]
        if roi.shape[0] < th or roi.shape[1] < tw:
            continue
        if cv2.minMaxLoc(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED))[1] < 0.8:
            n += 1
    return n


def extra_empty(img, xy):
    """Skills tab: True when the Extra slot at xy shows the green "+" of an empty slot."""
    t = _template('extra_empty.png')
    if t is None:
        return None
    th, tw = t.shape[:2]
    x, y = xy
    roi = img[y - th // 2 - 8:y + th // 2 + 8, x - tw // 2 - 8:x + tw // 2 + 8]
    if roi.shape[0] < th or roi.shape[1] < tw:
        return None
    return cv2.minMaxLoc(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED))[1] >= 0.8
