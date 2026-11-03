"""What the bot knows about Hunting Together RPG's screens.

All coordinates are for LDPlayer at 1280x720 (landscape).
"""
import collections
import difflib
import itertools
import os
import re
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# Buttons
MENU_BTN = (963, 50)
MENU_BAG = (860, 150)
MENU_BOOK = (768, 245)
BOOK_TAB_MONSTERS = (880, 38)
BOOK_TAB_CARDS = (990, 38)
BOOK_CLOSE = (1230, 38)
BAG_CLOSE = (1222, 110)
AUTO_GEAR = (76, 615)
AUTO_CARD = (170, 615)
DISMANTLE_MODE = (916, 605)               # hammer button in the Bag
DISMANTLE_SELECT_ALL = (1027, 605)
DISMANTLE_GO = (870, 605)                 # press twice: the second press confirms
DISMANTLE_CANCEL = (1169, 605)
DISMANTLE_OK = (640, 533)                 # "Dismantle Complete" popup
HUNT_BTN = (1094, 651)
CARD_SLOTS = [(357, 205), (510, 205), (662, 205), (815, 205)]
MONSTER_PREV = (61, 171)
MONSTER_NEXT = (440, 171)
MONSTER_THUMBS = [(85, 265), (192, 265), (298, 265), (405, 265)]
MENU_STATUS = (677, 150)                  # menu: Status & Skills
STATUS_TAB_STATS_WIDE = (703, 39)         # Stats tab when the window opened on Skills (it is wider there);
                                          # inside the button whether the window sits left or right (see below)
# The Stats tab positions below were measured with its "STR" label centred at x=STATS_STR_X.
# Status & Skills is not always in the same place: on 2026-11-04 it sat 66 px further left
# than on 2026-11-03 (Roblox's top bar had one icon less), so open_stats() measures the shift
# and every position below is moved by it (Game.sx).
STATS_STR_X = 388
STATS_CLEAR = (658, 152)
STATS_CONFIRM = (637, 659)
AUTO_UPGRADE_TOGGLE = (497, 659)          # the game's own "Auto Upgrade Status" switch
AUTO_UPGRADE_PIXEL = (372, 680)           # green while that switch is on, grey when off
STAT_PLUS_X = 658
ROBLOX_MENU = (57, 50)                    # Roblox logo: People / Leave / Respawn / Resume
ROBLOX_LEAVE = (253, 228)
ROBLOX_RESUME = (1025, 228)
ROBLOX_LEAVE_CONFIRM = (475, 384)         # "Are you sure you want to leave the game?" -> Leave

# OCR regions (x, y, w, h)
LEVEL_RECT = (0, 688, 130, 20)
AREA_LIST_RECT = (30, 95, 215, 600)
AREA_LIST_SCROLL = ((135, 600), (135, 260))
AREA_ROW_STEP = 73                        # px between rows of the Card Book area list
CARD_DROPS_RECT = (1020, 444, 230, 28)
MONSTER_AREA_RECT = (60, 140, 380, 60)
MONSTER_NAME_RECT = (636, 144, 600, 42)
MONSTER_TAGS_RECT = (636, 184, 420, 30)
MONSTER_SPAWN_RECT = (636, 214, 600, 40)
DISMANTLE_COUNT_RECT = (530, 590, 240, 30)
DISMANTLE_CANCEL_RECT = (1095, 582, 150, 46)  # "Cancel" in dismantle mode; outside it the same spot is Sell Many
MONSTER_STATS_RECT = (505, 280, 740, 62)
MONSTER_CARD_RECT = (580, 600, 420, 26)
MONSTER_STAT_COLUMNS = {'level': (505, 690), 'hp': (690, 875), 'atk': (875, 1060), 'exp': (1060, 1245)}
POINTS_LEFT_RECT = (360, 138, 215, 32)
BAG_TITLE_RECT = (150, 132, 190, 30)      # "Lv.31 Merchant" above the character in the Bag
BOSS_TIMER_RECT = (280, 60, 720, 260)     # floating "Respawns in 9:58" sign over a dead boss; moves with the camera
BOSS_WARN_RECT = (280, 95, 720, 45)       # red "<boss> uses <skill> — get out of the red ground!" under the boss bar
DIED_BUTTONS_RECT = (400, 290, 480, 130)  # You Died: green "Respawn here • 9 Robux" (never!) over blue "Respawn in town (free)"
TARGET_NAME_RECT = (440, 8, 420, 34)      # name of the monster being fought, over its HP bar (gone when Auto is off)
TARGET_HP_RECT = (512, 50, 256, 24)       # that monster's HP bar
AUTO_SWORD_RECT = (1150, 598, 95, 80)     # the Auto battle sword, bottom right (key F): green = on, white = off
CARD_NAME_RECT = (520, 166, 240, 30)      # name at the top of the card in the "Obtained!" popup
CARD_OWNED_RECT = (940, 420, 305, 26)     # Card Book > Card Details: "Owned  None yet" / "Owned  2 cards"
ROBLOX_LEAVE_RECT = (63, 198, 380, 60)
ROBLOX_CONFIRM_RECT = (300, 250, 680, 50)
STAT_NAMES = ('STR', 'AGI', 'VIT', 'INT', 'DEX', 'LUK')
STAT_ROW_Y = (220, 289, 358, 426, 495, 563)
STAT_VALUE_X = (516, 572)                 # the base value; the blue "+12" bonus starts right after
ELEMENTS =('Neutral', 'Fire', 'Water', 'Wind', 'Earth', 'Holy', 'Shadow', 'Ghost', 'Poison', 'Undead')

# Game keys (Linux key codes, sent through the emulator's keyboard device)
KEY_B = 48                                # Bag
KEY_N = 49                                # Adventure Book (opens on Card Book)
KEY_O = 24                                # Roblox camera: zoom out one step
KEYS = {'Q': 16, 'W': 17, 'E': 18, 'A': 30, 'S': 31, 'D': 32, 'F': 33}   # Q = roll, WASD = move

# Template name -> region where it is looked for (x, y, w, h)
TEMPLATE_REGIONS = {
    'hud_menu': (925, 20, 65, 65),        # 4-square menu icon on the HUD
    'hud_hp': (450, 620, 70, 50),         # "HP" on the HP bar
    'menu_quests': (550, 100, 72, 72),    # Quests icon = menu popup is open
    'book_icon': (240, 0, 200, 80),       # Adventure Book window (an update moved it from x~366 to ~299)
    'hunt_btn': (960, 620, 270, 70),      # Card Book: "Hunt this monster"
    'stop_btn': (960, 620, 270, 70),      # Card Book: "Stop" = this monster is already being hunted
    'bag_icon': (525, 75, 75, 75),        # Bag window
    'auto_gear': (25, 590, 110, 50),
    'dismantle_select_all': (960, 580, 140, 50),
    'dismantle_done': (330, 95, 300, 65),
    'close_x': (0, 0, 1280, 160),         # red X close buttons, searched at several sizes
    'close_x_status': (0, 0, 1280, 160),
    'stats_str': (200, 170, 360, 80),     # "STR" label = Status & Skills is on its Stats tab (x 322 or 388)
    'target_leader': (490, 5, 130, 50),   # orange "Leader" badge before the targeted monster's name
    'target_boss': (490, 5, 130, 50),     # same place, "Boss" (the two score ~0.81 on each other)
    'card_obtained': (530, 70, 220, 65),  # "Obtained!" over a new card; it closes by itself after ~5 s
    'skip_btn': (540, 600, 200, 100),     # "Skip" over the intro camera, shown for ~3 s as soon as a join has loaded
    'you_died': (380, 145, 180, 80),      # "You Died" window: the player was killed (the boss's badge goes too)
}
TEMPLATES = {name: cv2.imread(os.path.join(HERE, 'templates', name + '.png'))
             for name in TEMPLATE_REGIONS}
BOOK_X_REGION = (1195, 5, 75, 70)         # where close_x sits on the Adventure Book
BAG_X_REGION = (1190, 80, 65, 60)         # where close_x sits on the Bag
# Big digits of the Stats tab, cut from the game's own screen
GLYPH_H, GLYPH_W = 32, 26
DIGITS = {d: cv2.imread(os.path.join(HERE, 'templates', 'digits', f'{d}.png'), cv2.IMREAD_GRAYSCALE)
          .astype(np.float32) / 255 for d in '0123456789'}


# ---- what the bot did lately, for the control panel ---------------------------
# TRACE feeds the overlay drawn on the live screen (what was looked at, read, tapped);
# STEPS feeds the step timeline (how long each step took), also written to logs/steps.log.
TRACE = collections.deque(maxlen=400)
STEPS = collections.deque(maxlen=300)
_ids = itertools.count(1)
TRACED_TEMPLATES = {'target_boss': 'ป้าย Boss', 'target_leader': 'ป้าย Leader', 'card_obtained': 'Obtained!',
                    'hunt_btn': 'ปุ่ม Hunt', 'stop_btn': 'ปุ่ม Stop', 'stats_str': 'หน้า Stats',
                    'dismantle_select_all': 'Select All', 'dismantle_done': 'ย่อยเสร็จ', 'skip_btn': 'ปุ่ม Skip',
                    'you_died': 'You Died'}


def note(kind, **data):
    TRACE.append({'id': next(_ids), 't': round(time.time(), 2), 'kind': kind, **data})


def step(label, detail=''):
    """Start a named step; the one before it is closed off with how long it took.
    The same step again right after itself just keeps running (e.g. waiting between checks)."""
    now = time.time()
    if STEPS and STEPS[-1]['s'] is None and (STEPS[-1]['label'], STEPS[-1]['detail']) == (label, detail):
        return
    if STEPS and STEPS[-1]['s'] is None:
        last = STEPS[-1]
        last['s'] = round(now - last['t'], 1)
        try:
            os.makedirs(os.path.join(HERE, 'logs'), exist_ok=True)
            with open(os.path.join(HERE, 'logs', 'steps.log'), 'a', encoding='utf-8') as f:
                f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last["t"]))} '
                        f'{last["s"]:6.1f}s  {last["label"]} {last["detail"]}\n'.rstrip() + '\n')
        except OSError:
            pass
    STEPS.append({'id': next(_ids), 't': round(now, 2), 'label': label, 'detail': detail, 's': None})
    note('step', label=label, detail=detail)


def find(img, name, threshold=0.82, scales=(1.0,), region=None):
    """Best match of a template inside its region: (score, (cx, cy)) or (score, None)."""
    x, y, w, h = region or TEMPLATE_REGIONS[name]
    roi = img[y:y + h, x:x + w]
    best = (0.0, None)
    for s in scales:
        t = TEMPLATES[name] if s == 1.0 else cv2.resize(TEMPLATES[name], None, fx=s, fy=s)
        if t.shape[0] > roi.shape[0] or t.shape[1] > roi.shape[1]:
            continue
        res = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, (mx, my) = cv2.minMaxLoc(res)
        if score > best[0]:
            best = (score, (x + mx + t.shape[1] // 2, y + my + t.shape[0] // 2))
    if name in TRACED_TEMPLATES:
        note('look', rect=[x, y, w, h], label=TRACED_TEMPLATES[name], ok=best[0] >= threshold,
             score=round(best[0], 2))
    return best if best[0] >= threshold else (best[0], None)


def seen(img, name, threshold=0.82, region=None):
    return find(img, name, threshold, region=region)[1] is not None


def auto_battle(img):
    """True / False when the Auto battle sword is green (on) / white (off), None when it cannot
    be seen. On 2026-11-04: on = ~1,670 green px and ~90 white, off = 0 green and ~1,500 white."""
    x, y, w, h = AUTO_SWORD_RECT
    band = img[y:y + h, x:x + w].astype(np.int16)
    b, g, r = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    green = int(((g > 150) & (g - r > 60) & (g - b > 60)).sum())
    white = int(((r > 215) & (g > 215) & (b > 215)).sum())
    if max(green, white) < 600:
        return None
    return green > white


def target_hp(img):
    """Roughly how much of the fought monster's HP bar is red (0 when there is no bar)."""
    x, y, w, h = TARGET_HP_RECT
    band = img[y:y + h, x:x + w].astype(np.int16)
    b, g, r = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    red = (r > 150) & (g < 100) & (b < 100)
    return float((red.mean(axis=0) > 0.3).mean())


def app_page(img):
    """Roblox's own pages (home, game page) are portrait; the game is landscape."""
    return img.shape[0] > img.shape[1]


def target_kind(img):
    """'boss' or 'leader' when the targeted monster wears that badge, else None."""
    # the right badge scores 0.92-1.00, the other badge ~0.75-0.81, a normal monster ~0.2
    boss, leader = find(img, 'target_boss', 0.85)[0], find(img, 'target_leader', 0.85)[0]
    if max(boss, leader) < 0.85:
        return None
    return 'boss' if boss > leader else 'leader'


def screen_state(img):
    """'book', 'bag', 'menu', 'hud' or 'unknown'.

    Each screen is recognised by more than one mark: LDPlayer draws the Windows
    mouse pointer into its screenshots, and it can sit on top of any one of them.
    """
    if seen(img, 'book_icon') or seen(img, 'close_x', 0.85, BOOK_X_REGION):
        return 'book'
    if seen(img, 'bag_icon') + seen(img, 'auto_gear') + seen(img, 'close_x', 0.85, BAG_X_REGION) >= 2:
        return 'bag'
    if seen(img, 'menu_quests'):
        return 'menu'
    if seen(img, 'hud_menu') or seen(img, 'hud_hp'):
        return 'hud'
    return 'unknown'


def exp_fraction(img):
    """Fill of the green EXP bar along the bottom edge (0..1). Only valid on the plain HUD."""
    row = img[712].astype(int)
    b, g, r = row[:, 0], row[:, 1], row[:, 2]
    xs = np.where((g > 150) & (g > r + 40) & (g > b + 40))[0]
    return (xs.max() + 1) / img.shape[1] if len(xs) else 0.0


L_CHARS = 'lLiI|t!'   # what OCR turns the last letter of "Level" into


def parse_level(text, last=None):
    """'Level 34 • 50.7%' -> 34. OCR often reads the 'l' of 'Level' as '1' ('Leve134')."""
    m = re.search(r'Le[vy]e', text)
    if not m:
        return None
    tokens = text[m.end():].split()
    head = []
    for tok in tokens:
        if '%' in tok:
            break
        head.append(tok)
    if len(head) == len(tokens):          # no percent seen: trust only the first token(s)
        head = head[:2] if head and head[0] in ('1',) + tuple(L_CHARS) else head[:1]
    token = ''.join(t for t in head if re.fullmatch(rf'[0-9{re.escape(L_CHARS)}]+', t))
    explicit = bool(token) and token[0] in L_CHARS
    digits = token.lstrip(L_CHARS)
    if explicit and not digits and len(token) > 1:
        digits = '1' * (len(token) - 1)   # "Level 1" read as "Levell" or "Level I"
    digits = re.sub(rf'[{re.escape(L_CHARS)}]', '1', digits)   # "Level 1I": an I after a digit is a 1
    if not digits.isdigit():
        return None
    if explicit:
        level = int(digits)
    elif len(digits) == 3:
        level = int(digits[1:])
    elif len(digits) == 2 and digits[0] == '1':
        # "Leve15" is either "Level 15" or "Level 5" with the l read as 1
        both = [int(digits), int(digits[1])]
        level = min(both, key=lambda v: abs(v - last)) if last else both[1]
    else:
        level = int(digits)
    return level if 1 <= level <= 200 else None


def norm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def similar(a, b):
    """0..1 likeness of two names, ignoring case, spaces and punctuation."""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def same_name(a, b):
    return similar(a, b) >= 0.9


def best_match(text, names, threshold=0.8, margin=0.0):
    """Index of the name in `names` that `text` reads as, or None.
    margin: how far ahead of the runner-up the best name must be ("Boer Forest" is
    as close to "Boar Forest" as to "Bear Forest", so it names neither)."""
    scores = [similar(text, n) for n in names]
    if not scores or max(scores) < threshold:
        return None
    best = scores.index(max(scores))
    runner_up = max((s for i, s in enumerate(scores) if i != best), default=0.0)
    return best if scores[best] - runner_up >= margin else None


def number(text):
    m = re.search(r'\d[\d,]*', text.replace('.', ','))
    return int(m.group(0).replace(',', '')) if m else None


# ---- stats -----------------------------------------------------------------
def _glyph(mask):
    """One digit's mask scaled to GLYPH_H high and centred in GLYPH_W, like templates/digits."""
    h, w = mask.shape
    r = cv2.resize(mask.astype(np.float32), (min(GLYPH_W, max(1, round(w * GLYPH_H / h))), GLYPH_H),
                   interpolation=cv2.INTER_AREA)
    out = np.zeros((GLYPH_H, GLYPH_W), np.float32)
    off = (GLYPH_W - r.shape[1]) // 2
    out[:, off:off + r.shape[1]] = r
    return out


def _likeness(a, b):
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))


def _split_digits(mask, x, w):
    """Column ranges of the digits in one blob: two digits can touch ("44"), and one digit
    is never wider than ~20 px, so a wider blob is cut where it has the least ink."""
    if w <= 24:
        return [(x, w)]
    cols = mask[:, x:x + w].sum(axis=0)
    lo, hi = round(w * 0.3), round(w * 0.7)
    cut = lo + int(np.argmin(cols[lo:hi]))
    return _split_digits(mask, x, cut) + _split_digits(mask, x + cut, w - cut)


def read_stat_value(img, y, dx=0):
    """A base stat on the Stats tab, or None (dx: how far the window is from where STAT_VALUE_X
    was measured). Windows OCR skips a lone digit ("9"), so each digit is matched against
    templates/digits instead."""
    x0, x1 = STAT_VALUE_X[0] + dx, STAT_VALUE_X[1] + dx
    crop = cv2.cvtColor(img[y - 22:y + 22, x0:x1], cv2.COLOR_BGR2GRAY)
    mask = (crop < 90).astype(np.uint8)
    _, _, boxes, _ = cv2.connectedComponentsWithStats(mask, 8)
    # digits are ~20 px tall and start near the top; the green "+3" of a preview sits lower
    boxes = sorted((b for b in boxes[1:] if b[3] >= 14 and b[1] <= 16), key=lambda b: b[0])
    if not boxes:
        return None
    digits = ''
    for x, w, y0, h in ((cx, cw, b[1], b[3]) for b in boxes for cx, cw in _split_digits(mask, b[0], b[2])):
        part = mask[y0:y0 + h, x:x + w]
        ys, xs = np.nonzero(part)
        if not len(xs):
            return None
        part = part[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        scores = sorted(((_likeness(_glyph(part), t), d) for d, t in DIGITS.items()), reverse=True)
        (best, digit), (second, _) = scores[0], scores[1]
        if best < 0.6 or best - second < 0.08:
            return None
        digits += digit
    return int(digits)


def parse_points(text):
    """'Points left 18' -> 18 (OCR reads 0 as O and 1 as l)."""
    m = re.search(r'(?i)left\s*([0-9OoIl|]+)', text)
    if not m:
        return None
    return int(m.group(1).translate(str.maketrans('OoIl|', '00111')))


def parse_respawn(text):
    """'Respawns in 9:58' -> 598 seconds, or None. OCR often loses the start ("spawns ih 9:43")."""
    m = re.search(r'(?i)(?:awn|wns)\w*\s*\w{0,3}\s*(\d{1,2})\s*[:.;]\s*(\d\d)', text)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def parse_owned(text):
    """'Owned None yet' -> 0, 'Owned 2 cards' -> 2, None when it could not be read."""
    if 'none' in text.lower():
        return 0
    m = re.search(r'(?i)owned\D*(\d+)', text)
    return int(m.group(1)) if m else None


def parse_job(text):
    """'Lv.31 Merchant' -> 'Merchant'."""
    m = re.search(r'Lv\W*\d+\s+([A-Za-z][A-Za-z ]*[A-Za-z])', text)
    return m.group(1) if m else None


def stat_cost(value):
    """Points to raise a stat from `value` to `value + 1` (same as Ragnarok)."""
    return (value - 1) // 10 + 2


def stat_points_total(level):
    """Stat points earned by `level` (Lv.99: 1244, matching the game)."""
    return sum(lv // 5 + 3 for lv in range(2, level + 1))


def stat_points_spent(values):
    return sum(stat_cost(v) for s in values.values() for v in range(1, s))


def plan_stats(current, points, rows):
    """How many times to press + on each stat to follow the stat table.

    Works on the first row whose targets are not all reached. Inside a row the stat
    furthest behind (counted from the row before) goes first, so they arrive together.
    When that stat costs more than the points left, the rest waits for the next level.
    """
    cur = dict(current)
    add = dict.fromkeys(STAT_NAMES, 0)
    base = dict.fromkeys(STAT_NAMES, 1)
    for row in rows:
        goal = {s: row[s] for s in STAT_NAMES if row.get(s)}
        while True:
            behind = [s for s in goal if cur[s] < goal[s]]
            if not behind:
                break
            s = min(behind, key=lambda s: (cur[s] - base[s]) / max(1, goal[s] - base[s]))
            if stat_cost(cur[s]) > points:
                return add
            points -= stat_cost(cur[s])
            cur[s] += 1
            add[s] += 1
        for s, v in goal.items():
            base[s] = max(base[s], v)
    return add


RECT_LABELS = {LEVEL_RECT: 'เลเวล', BOSS_TIMER_RECT: 'เวลาเกิดบอส', CARD_NAME_RECT: 'ชื่อการ์ด',
               CARD_OWNED_RECT: 'Owned', AREA_LIST_RECT: 'รายชื่อแมพ', CARD_DROPS_RECT: 'Drops from',
               POINTS_LEFT_RECT: 'แต้มเหลือ', ROBLOX_LEAVE_RECT: 'ปุ่ม Leave', ROBLOX_CONFIRM_RECT: 'ยืนยัน Leave',
               BAG_TITLE_RECT: 'อาชีพ', DISMANTLE_COUNT_RECT: 'จำนวนที่ย่อย', TARGET_NAME_RECT: 'มอนที่ตีอยู่'}


class TracedOcr:
    """Ocr that also notes which part of the screen it read and what it got, for the overlay."""
    def __init__(self, ocr):
        self.ocr = ocr

    def read(self, img, rect=None, scale=2.0, mode='gray'):
        lines = self.ocr.read(img, rect, scale, mode)
        area = list(rect) if rect else [0, 0, img.shape[1], img.shape[0]]
        note('read', rect=area, label=RECT_LABELS.get(tuple(area), 'อ่านตัวหนังสือ'),
             text=' '.join(l.text for l in lines)[:40])
        return lines

    def text(self, img, rect=None, scale=2.0, mode='gray'):
        return ' '.join(l.text for l in self.read(img, rect, scale, mode))

    def __getattr__(self, name):
        return getattr(self.ocr, name)


class Game:
    def __init__(self, dev, ocr, log):
        self.dev = dev
        self.ocr = TracedOcr(ocr)
        self.log = log
        self.img = None
        self.dismantled = 0                 # pieces of gear dismantled since start
        self.job = None                     # class name read from the Bag, e.g. 'Merchant'
        self.owned = None                   # copies of the card hunt() last opened, from its "Owned" line
        self.stats = None                   # base stats last read from the Stats tab
        self.stats_dx = 0                   # how far Status & Skills sits from where its positions were measured
        self.img_at = 0.0                   # when self.img was taken (the panel reuses it as its preview)

    # ---- basics ---------------------------------------------------------
    def shot(self):
        self.img = self.dev.screenshot()
        self.img_at = time.time()
        return self.img

    def state(self):
        return screen_state(self.shot())

    def tap(self, xy, wait=0.8):
        note('tap', xy=list(xy))
        self.dev.tap(*xy)
        time.sleep(wait)

    def key(self, code):
        note('key', label={KEY_B: 'B', KEY_N: 'N', KEY_O: 'O', **{v: k for k, v in KEYS.items()}}.get(code, str(code)))
        self.dev.key(code)

    def swipe(self, a, b, ms=700):
        note('swipe', a=list(a), b=list(b))
        self.dev.swipe(*a, *b, ms)

    def zoom_out(self, presses):
        """Zoom the camera out with Roblox's O key, sent like B/N so it works while LDPlayer is not
        the focused window (the mouse wheel did not). About 10 presses show a boss's respawn sign."""
        note('key', label=f'O ×{presses}')
        for _ in range(presses):
            self.dev.key(KEY_O)
            time.sleep(0.12)

    def wait_state(self, want, timeout=8.0):
        want = (want,) if isinstance(want, str) else want
        end = time.time() + timeout
        while True:
            s = self.state()
            if s in want or time.time() > end:
                return s in want
            time.sleep(0.4)

    def level(self, last=None, img=None):
        """(level or None, exp fraction) from a screenshot of the plain HUD (default: the last one)."""
        img = self.img if img is None else img
        level = None
        # the text is white but the scene's lighting can tint it, so 'white' alone is not enough
        for scale, mode in ((4, 'gray'), (4, 'white'), (3, 'gray'), (3, 'white'), (5, 'gray')):
            level = parse_level(self.ocr.text(img, LEVEL_RECT, scale, mode), last)
            if level:
                break
        return level, exp_fraction(img)

    def close_any(self):
        """Tap a red X close button if one is visible. True if something was tapped."""
        for name in ('close_x', 'close_x_status'):
            _, xy = find(self.img, name, 0.8, scales=(1.2, 1.0, 0.85, 0.7, 0.6))
            if xy:
                self.tap(xy)
                return True
        return False

    def to_hud(self, tries=6):
        """Close whatever window is open until the plain HUD shows."""
        for _ in range(tries):
            s = self.state()
            if s == 'hud':
                return True
            if s == 'book':
                self.tap(BOOK_CLOSE)
            elif s == 'bag':
                self.tap(BAG_CLOSE)
            elif s == 'menu':
                self.tap(MENU_BTN)
            elif seen(self.img, 'dismantle_done'):
                self.tap(DISMANTLE_OK)
            elif not self.close_any():
                time.sleep(1.5)
        return self.state() == 'hud'

    def open_menu_item(self, item, want):
        for _ in range(3):
            if not self.to_hud():
                continue
            self.tap(MENU_BTN, 1.0)
            if not self.wait_state('menu', 4):
                continue
            self.tap(item, 1.2)
            if self.wait_state(want, 6):
                return True
        return False

    def open_window(self, key, want, menu_item):
        """Open the Bag (B) or Adventure Book (N) with its hotkey; fall back to the menu."""
        for _ in range(2):
            if not self.to_hud():
                continue
            self.key(key)
            if self.wait_state(want, 3):
                return True
        self.log(f'กดปุ่ม {"B" if key == KEY_B else "N"} แล้วหน้าต่างไม่ขึ้น เปิดผ่านเมนูแทน')
        return self.open_menu_item(menu_item, want)

    # ---- Adventure Book -> Card Book -> Hunt this monster ----------------
    def _select_area(self, area, known):
        """Scroll the Card Book area list until `area` shows, then tap it.

        `known` is every area name in list order (from the scan). OCR cannot read some
        names ("Rabbit" in "Rabbit Field"), so the target can also be found by counting
        rows from a neighbour that it can read.
        """
        target = best_match(area, known, 0.9) if known else None
        previous = None
        direction = 1
        top, bottom = AREA_LIST_RECT[1] + 10, AREA_LIST_RECT[1] + AREA_LIST_RECT[3] - 15
        for _ in range(30):
            lines = self.ocr.read(self.shot(), AREA_LIST_RECT, scale=2.5)
            if target is not None:
                hits = [l for l in lines if best_match(l.text, known, 0.8, 0.05) == target]
            else:
                hits = [l for l in lines if similar(l.text, area) >= 0.8]
            if hits:
                self.tap(max(hits, key=lambda l: similar(l.text, area)).center, 1.0)
                return True
            if target is not None:
                anchors = [(i, l) for l in lines if (i := best_match(l.text, known, 0.85, 0.05)) is not None]
                if anchors:
                    i, line = min(anchors, key=lambda a: abs(a[0] - target))
                    x, y = line.center
                    y += (target - i) * AREA_ROW_STEP
                    if top < y < bottom:
                        self.tap((x, y), 1.0)
                        return True
                    direction = 1 if target > i else -1
            page = tuple(norm(l.text) for l in lines)
            if page == previous:            # the list did not move: hit an end
                if direction == -1:
                    return False
                direction = -1
            previous = page
            (x1, y1), (x2, y2) = AREA_LIST_SCROLL
            if direction == 1:
                self.swipe((x1, y1), (x2, y2))
            else:
                self.swipe((x2, y2), (x1, y1))
            time.sleep(0.8)
        return False

    def scan_card_owned(self, card_areas, known):
        """Walk Adventure Book > Card Book and read how many copies of each card we own.
        card_areas: [(area, [card monsters in their slot order])]; known: every area in list
        order (for _select_area). Returns {monster: copies} for the cards it could read."""
        owned = {}
        step('อ่านการ์ดที่มี')
        if not self.open_window(KEY_N, 'book', MENU_BOOK):
            self.log('เปิด Adventure Book ไม่ได้')
            return owned
        self.tap(BOOK_TAB_CARDS, 1.2)
        try:
            for area, names in card_areas:
                if not self._select_area(area, known):
                    self.log(f'หาแมพ "{area}" ในหน้า Card Book ไม่เจอ ข้ามไป')
                    continue
                time.sleep(0.6)
                for xy in CARD_SLOTS[:len(names)]:
                    self.tap(xy, 0.8)
                    img = self.shot()
                    who = None
                    for scale, mode in ((2.5, 'dark'), (3, 'gray')):
                        who = best_match(self.ocr.text(img, CARD_DROPS_RECT, scale, mode), names, 0.6, 0.02)
                        if who is not None:
                            break
                    copies = parse_owned(self.ocr.text(img, CARD_OWNED_RECT, 2))
                    if copies is None:
                        copies = parse_owned(self.ocr.text(img, CARD_OWNED_RECT, 2, 'dark'))
                    if who is not None and copies is not None:
                        owned[names[who]] = copies
        finally:
            self.to_hud()
        return owned

    def _select_card(self, monster, order=()):
        """Tap the card whose "Drops from" names `monster` best ("Saber Tiger" must not
        settle for "Leader Saber Tiger"). When no card's line can be read, `order` (the area's
        monsters in data/monsters.json order, which is the cards' order) picks it instead."""
        best = (0.0, None)
        for xy in CARD_SLOTS:
            self.tap(xy, 0.7)
            img = self.shot()
            score = 0.0
            for scale, mode in ((2.5, 'dark'), (3, 'gray')):
                drops = self.ocr.text(img, CARD_DROPS_RECT, scale, mode)
                score = max(score, similar(re.sub(r'(?i)^.*from', '', drops), monster))
                if score >= 0.95:
                    return True
            if score > best[0]:
                best = (score, xy)
        if best[0] < 0.75:
            # an item tooltip from the Bag can stay stuck over "Drops from" (seen 2026-11-04)
            i = list(order).index(monster) if monster in order else len(CARD_SLOTS)
            if i >= len(CARD_SLOTS):
                return False
            self.log(f'อ่านบรรทัด "Drops from" ไม่ออก (อาจมีป้ายของในกระเป๋าค้างทับ) '
                     f'เลือกการ์ดใบที่ {i + 1} ตามลำดับมอนในแมพแทน')
            self.tap(CARD_SLOTS[i], 0.7)
            return True
        self.tap(best[1], 0.7)
        return True

    def hunt(self, area, monster, known_areas=(), order=()):
        """Open the Card Book on `monster`'s card and press Hunt this monster.

        When that monster is already being hunted the button reads "Stop" instead,
        and pressing it would end the hunt, so then nothing is pressed.
        """
        self.owned = None
        img = self._open_card(area, monster, known_areas, order)
        if img is None:
            return False
        self.owned = parse_owned(self.ocr.text(img, CARD_OWNED_RECT, 2))
        if seen(img, 'stop_btn'):
            self.log(f'กำลังตี {monster} อยู่แล้ว ไม่ต้องกด Hunt ซ้ำ')
            return self.to_hud()
        if not seen(img, 'hunt_btn', 0.7):
            self.log('ไม่เห็นปุ่ม Hunt this monster')
            self.to_hud()
            return False
        step('กด Hunt', monster)
        self.tap(HUNT_BTN, 1.5)
        return self.to_hud()

    def stop_hunt(self, area, monster, known_areas=()):
        """Press Stop on the hunted card in the Card Book, ending Hunt this monster: while a
        hunt is on, Auto goes for that monster only, even after it is switched off and on.

        The hunted card is the only one whose button reads Stop, so no card name is read: an
        item tooltip from the Bag can stay stuck over Card Details and hide "Drops from".
        The book reopens on the card it showed last, usually that one; otherwise every card
        of `area` is tried. True when the button reads Hunt again (or no card was hunted)."""
        step('เปิด Adventure Book')
        if not self.open_window(KEY_N, 'book', MENU_BOOK):
            self.log('เปิด Adventure Book ไม่ได้')
            return False
        try:
            self.tap(BOOK_TAB_CARDS, 1.2)
            img = self.shot()
            if not seen(img, 'stop_btn'):
                step('เลือกแมพ', area)
                if not self._select_area(area, known_areas):
                    self.log(f'หาแมพ "{area}" ในหน้า Card Book ไม่เจอ')
                    return False
                for xy in CARD_SLOTS:
                    self.tap(xy, 0.7)
                    img = self.shot()
                    if seen(img, 'stop_btn'):
                        break
                else:
                    self.log(f'ไม่มีการ์ดไหนในแมพ {area} ที่ปุ่มเป็น Stop (ไม่ได้ Hunt อยู่)')
                    return True
            _, xy = find(img, 'stop_btn')
            step('กด Stop', monster)
            self.tap(xy, 1.2)
            return seen(self.shot(), 'hunt_btn', 0.7)
        finally:
            self.to_hud()

    def _open_card(self, area, monster, known_areas, order=()):
        """The Card Book open on `monster`'s card: its screenshot, or None (back on the HUD)."""
        step('เปิด Adventure Book')
        if not self.open_window(KEY_N, 'book', MENU_BOOK):
            self.log('เปิด Adventure Book ไม่ได้')
            return None
        self.tap(BOOK_TAB_CARDS, 1.2)
        step('เลือกแมพ', area)
        if not self._select_area(area, known_areas):
            self.log(f'หาแมพ "{area}" ในหน้า Card Book ไม่เจอ')
            self.to_hud()
            return None
        step('เลือกการ์ด', monster)
        if not self._select_card(monster, order):
            self.log(f'หาการ์ดของ "{monster}" ในแมพ {area} ไม่เจอ')
            self.to_hud()
            return None
        return self.shot()

    def fighting(self, monster, looks=5):
        """True when the target bar names `monster` and its HP bar moves within a few looks
        (~3 s): we are hitting it, not still walking towards it. Kills are quick, so a fresh
        monster's bar can sit full for 2-3 s before the first hit lands."""
        named, hps = False, set()
        for _ in range(looks):
            img = self.shot()
            if not named:
                name = self.ocr.text(img, TARGET_NAME_RECT, 2, 'white')
                named = monster.lower() in name.lower() or similar(name, monster) >= 0.8
            hp = round(target_hp(img), 2)
            if hp:                              # 0 = no bar in this shot
                hps.add(hp)
            if named and len(hps) > 1:
                return True
            time.sleep(0.5)
        return False

    def auto_all(self):
        """Auto battle off and on again (key F twice). After Hunt this monster, Auto only goes
        for that one monster; switched on afresh it fights every monster around. True when
        Auto ends up on."""
        state = auto_battle(self.shot())
        if state is None:
            return False
        for want in ([False] if state else []) + [True]:
            for _ in range(2):                  # a key press now and then does not register
                self.key(KEYS['F'])
                time.sleep(1.0)
                if auto_battle(self.shot()) == want:
                    break
            else:
                return False
        return True

    # ---- Bag -> Auto Gear / Auto Card -----------------------------------
    def auto_equip(self, dismantle=False):
        """Auto Gear + Auto Card, then optionally dismantle the leftover gear.
        Auto Gear always goes first so a better piece is worn before the rest is dismantled."""
        step('เปิดกระเป๋า')
        if not self.open_window(KEY_B, 'bag', MENU_BAG):
            self.log('เปิดกระเป๋าไม่ได้')
            return False
        self._read_job()
        step('Auto Gear + Auto Card')
        self.tap(AUTO_GEAR, 1.2)
        self.tap(AUTO_CARD, 1.2)
        if dismantle:
            step('ย่อยของ')
            self._dismantle()
        self.tap(BAG_CLOSE, 1.0)
        return self.to_hud()

    def _dismantle(self):
        """Bag must be open. Hammer -> Select All -> Dismantle, Dismantle -> OK."""
        self.tap(DISMANTLE_MODE, 1.2)
        if not seen(self.shot(), 'dismantle_select_all'):
            self.log('ไม่เจอปุ่ม Select All ในโหมดย่อยของ ข้ามการย่อย')
            self._cancel_dismantle()
            return False
        self.tap(DISMANTLE_SELECT_ALL, 1.2)
        count = number(self.ocr.text(self.shot(), DISMANTLE_COUNT_RECT, 2.5))
        if not count:
            self.log('ไม่มีของให้ย่อย')
            self._cancel_dismantle()
            return True
        # the confirm state times out after a second or two, so the two presses go back to back
        self.tap(DISMANTLE_GO, 0.6)
        self.tap(DISMANTLE_GO, 0)
        end = time.time() + 5
        while time.time() < end:
            time.sleep(0.5)
            if seen(self.shot(), 'dismantle_done'):
                self.tap(DISMANTLE_OK, 1.2)
                self.dismantled += count
                self.log(f'ย่อยของ {count} ชิ้น')
                return True
        self.log('กด Dismantle แล้วแต่ไม่เห็นหน้าต่าง Dismantle Complete')
        self._cancel_dismantle()
        return False

    def _cancel_dismantle(self):
        """Leave dismantle mode with its Cancel button, but only when the button there reads
        Cancel: outside dismantle mode the same spot is Sell Many, which the bot never presses."""
        text = self.ocr.text(self.shot(), DISMANTLE_CANCEL_RECT, 2, 'white')
        if 'cancel' in text.lower():
            self.tap(DISMANTLE_CANCEL, 1.0)
            return True
        self.log(f'ปุ่มตรง Cancel เขียนว่า "{text}" ไม่ใช่ Cancel เลยไม่กด')
        return False

    def _read_job(self):
        """Bag must be open. Remember the class from the title above the character."""
        img = self.shot()
        for mode in ('gray', 'dark'):
            job = parse_job(self.ocr.text(img, BAG_TITLE_RECT, 2, mode))
            if job:
                if job != self.job:
                    self.log(f'อาชีพตอนนี้: {job}')
                self.job = job
                return job
        return self.job

    def read_job(self):
        """Open the Bag just to see which class is being played."""
        if not self.open_window(KEY_B, 'bag', MENU_BAG):
            return None
        job = self._read_job()
        self.tap(BAG_CLOSE, 1.0)
        self.to_hud()
        return job

    # ---- Status & Skills -> Stats ----------------------------------------
    def open_stats(self):
        """Open Status & Skills on its Stats tab. True when the stat rows show.

        The window remembers its last tab; on Skills it is wider and its red X sits where
        the Adventure Book's does, so screen_state() calls it 'book'. Also measures where
        the window is (stats_dx): it moves sideways now and then.
        """
        for _ in range(2):
            if not self.to_hud():
                continue
            self.tap(MENU_BTN, 1.0)
            if not self.wait_state('menu', 4):
                continue
            self.tap(MENU_STATUS, 1.5)
            end = time.time() + 4
            while time.time() < end:
                img = self.shot()
                if seen(img, 'stats_str'):
                    time.sleep(1.0)         # let it finish sliding in before pressing anything
                    _, xy = find(self.shot(), 'stats_str')
                    if xy is None:
                        continue
                    self.stats_dx = xy[0] - STATS_STR_X
                    return True
                if screen_state(img) == 'book':
                    self.tap(STATUS_TAB_STATS_WIDE, 1.2)
                time.sleep(0.4)
        return False

    def sx(self, xy):
        """A Stats tab position moved to where the window is now (see STATS_STR_X)."""
        return xy[0] + self.stats_dx, xy[1]

    def read_stats(self, img):
        """({stat: base value}, points left), with None for what could not be read."""
        values = {s: read_stat_value(img, y, self.stats_dx) for s, y in zip(STAT_NAMES, STAT_ROW_Y)}
        points = None
        x, y, w, h = POINTS_LEFT_RECT
        for scale, mode in ((3, 'gray'), (2, 'dark'), (2, 'gray')):
            points = parse_points(self.ocr.text(img, (x + self.stats_dx, y, w, h), scale, mode))
            if points is not None:
                break
        return (values if None not in values.values() else None), points

    def raise_stats(self, rows, level):
        """Spend stat points by the stat table (see plan_stats), then confirm.

        Turns off the game's own Auto Upgrade Status first, or it would spend the points
        on its own picks at every level-up. Nothing is confirmed unless the points left
        after pressing + are exactly what the plan expects. True when it went fine
        (also when there was nothing to spend).
        """
        step('เปิดหน้า Stats')
        if not self.open_stats():
            self.log('เปิดหน้า Stats ไม่ได้')
            self.to_hud()
            return False
        try:
            img = self.img
            px, py = self.sx(AUTO_UPGRADE_PIXEL)
            b, g, r = (int(c) for c in img[py, px])
            if g - max(b, r) > 50:
                self.tap(self.sx(AUTO_UPGRADE_TOGGLE), 1.2)
                self.log('ปิด Auto Upgrade Status ของเกม บอทจะอัพ Stats เองตามตาราง')
                img = self.shot()
            values, points = self.read_stats(img)
            if values is None or points is None:
                self.log(f'อ่านค่า Stats ไม่ได้ ({self._save("stats-unread")})')
                return False
            self.stats = values
            # the HUD's level text stays visible beside the window, so this is the level right now
            level = self.level(level, img)[0] or level
            if level and stat_points_spent(values) + points != stat_points_total(level):
                # a misread digit would put points in the wrong place, and that cannot be undone
                self.log(f'ค่า Stats ที่อ่านได้ไม่ตรงกับแต้มของ Lv.{level} ไม่อัพ ({self._save("stats-mismatch")})')
                return False
            add = plan_stats(values, points, rows)
            spend = sum(stat_cost(values[s] + i) for s, n in add.items() for i in range(n))
            if not spend:
                return True
            step('กด + ลงแต้ม', ' '.join(f'{s} +{n}' for s, n in add.items() if n))
            # a + pressed while the window is still settling can be missed: then Clear and go slower
            for gap in (0.45, 0.8):
                for s, y in zip(STAT_NAMES, STAT_ROW_Y):
                    for _ in range(add[s]):
                        self.tap(self.sx((STAT_PLUS_X, y)), gap)
                time.sleep(0.8)
                _, left = self.read_stats(self.shot())
                if left == points - spend:
                    break
                again = gap == 0.45
                self.log(f'กด + แล้วแต้มเหลือ {left} ไม่ตรงกับที่คิดไว้ ({points - spend}) กด Clear'
                         + (' แล้วลองใหม่ช้าลง' if again else f' แล้วไม่อัพ ({self._save("stats-press")})'))
                self.tap(self.sx(STATS_CLEAR), 1.2)
                if not again:
                    return False
            self.tap(self.sx(STATS_CONFIRM), 1.5)
            after, _ = self.read_stats(self.shot())
            want = {s: values[s] + add[s] for s in STAT_NAMES}
            self.stats = after or want
            text = ' '.join(f'{s} +{n}' for s, n in add.items() if n)
            now = ' · '.join(f'{s} {want[s]}' for s in STAT_NAMES if add[s])
            if after != want:
                self.log(f'อัพ Stats {text} แล้ว แต่ค่าที่อ่านได้หลังยืนยันไม่ตรง ({self._save("stats-after")})')
                return False
            self.log(f'อัพ Stats {text} ({now})')
            return True
        finally:
            self.to_hud()

    # ---- leaving the server / boss rooms / card drops ----------------------
    def leave(self):
        """Leave the server through Roblox's own menu, so the game gets to save
        (force-stopping the app once lost a chunk of EXP). True once Roblox shows its app page."""
        step('ออกจากห้อง (Leave)')
        if not self.to_hud():
            return False
        self.tap(ROBLOX_MENU, 1.0)
        for _ in range(5):                  # the menu can take a moment to open
            if similar(self.ocr.text(self.shot(), ROBLOX_LEAVE_RECT, 2), 'Leave') >= 0.8:
                break
            time.sleep(0.6)
        else:
            self.log(f'เปิดเมนู Roblox แล้วไม่เห็นปุ่ม Leave ({self._save("leave-menu")})')
            self.tap(ROBLOX_MENU, 1.0)
            return False
        self.tap(ROBLOX_LEAVE, 1.0)
        for _ in range(5):                  # the question can take a moment to show
            if 'leave the game' in self.ocr.text(self.shot(), ROBLOX_CONFIRM_RECT, 1.5).lower():
                break
            time.sleep(0.6)
        else:
            self.log(f'กด Leave แล้วไม่เห็นหน้าต่างถามยืนยัน ({self._save("leave")})')
            self.tap(ROBLOX_RESUME, 1.0)
            return False
        self.tap(ROBLOX_LEAVE_CONFIRM, 1.0)
        end = time.time() + 15
        while time.time() < end:
            if app_page(self.shot()):
                return True
            time.sleep(1)
        return False

    def card_popup(self, img):
        """Name on the card of an "Obtained!" popup, '' if it shows but the name is unreadable,
        or None when there is no popup."""
        if not seen(img, 'card_obtained', 0.8):
            return None
        for mode in ('gray', 'white'):
            name = self.ocr.text(img, CARD_NAME_RECT, 2, mode).strip()
            if name:
                return name
        return ''

    def boss_warning(self, img, skills):
        """Which of `skills` the boss announces right now, or None. The warning is red text over
        a busy scene, so only its red pixels are read (plain OCR missed most of them)."""
        x, y, w, h = BOSS_WARN_RECT
        band = img[y:y + h, x:x + w].astype(np.int16)
        b, g, r = band[:, :, 0], band[:, :, 1], band[:, :, 2]
        red = ((r > 170) & (r - g > 70) & (r - b > 40)).astype(np.uint8) * 255
        if cv2.countNonZero(red) < 800:
            return None
        raw = getattr(self.ocr, 'ocr', self.ocr)     # the mask is a crop: note the real rect below instead
        text = raw.text(cv2.cvtColor(255 - red, cv2.COLOR_GRAY2BGR), None, 2, 'color').lower()
        note('read', rect=list(BOSS_WARN_RECT), label='ท่าบอส', text=text[:40])
        # the skill's last word survives OCR best ("Nagma Bite", "Shelt Fortress")
        return next((s for s in skills if s.split()[-1].lower() in text), None)

    def respawn_in_town(self, img):
        """On the "You Died" window, tap "Respawn in town (free)". Only a line read as that
        button is tapped: the green button right above it costs Robux. True if tapped."""
        for line in self.ocr.read(img, DIED_BUTTONS_RECT, 1.5, 'gray'):
            text = line.text.lower()
            if 'town' in text and 'free' in text and 'robux' not in text:
                self.tap(line.center, 2)
                return True
        return False

    def press(self, key, times=1, gap=0.15):
        note('key', label=f'{key} ×{times}' if times > 1 else key)
        for i in range(times):
            self.dev.key(KEYS[key])
            if i + 1 < times:
                time.sleep(gap)

    def hold(self, key, seconds):
        note('key', label=f'กด {key} ค้าง {seconds:g} วิ')
        self.dev.hold(KEYS[key], seconds)

    def boss_respawn(self, img):
        """Seconds until the boss of this room respawns, from its floating sign, or None.
        x1.5 reads it best, but now and then splits "Respawns in" from "0:06": then x2 once more
        (a frame without the sign has no time in it, so it costs nothing there)."""
        text = ' | '.join(l.text for l in self.ocr.read(img, BOSS_TIMER_RECT, 1.5, 'gray'))
        left = parse_respawn(text)
        if left is None and re.search(r'\d\s*[:.;]\s*\d\d', text):
            left = parse_respawn(' | '.join(l.text for l in self.ocr.read(img, BOSS_TIMER_RECT, 2.0, 'gray')))
        return left

    def _save(self, tag):
        from bot import save_screen     # bot imports game, so this import waits until it is needed
        return save_screen(self.img, tag)

    # ---- Adventure Book -> Monsters (data scan) --------------------------
    def _monster_area(self):
        lines = self.ocr.read(self.shot(), MONSTER_AREA_RECT, scale=2)
        name = lines[0].text if lines else ''
        lv = re.findall(r'\d+', ' '.join(l.text for l in lines[1:]))
        return name, (int(lv[0]) if lv else None), (int(lv[1]) if len(lv) > 1 else None)

    def _monster_stats(self, img):
        stats = dict.fromkeys(MONSTER_STAT_COLUMNS)
        # small lone numbers are hard for Windows OCR, so try a few renderings
        for scale, mode in ((1.5, 'gray'), (2, 'gray'), (2.5, 'gray')):
            for l in self.ocr.read(img, MONSTER_STATS_RECT, scale, mode):
                if not re.fullmatch(r'[\d,.]+', l.text.strip()):
                    continue
                cx = l.x + l.w / 2
                for key, (a, b) in MONSTER_STAT_COLUMNS.items():
                    if a <= cx < b and stats[key] is None:
                        stats[key] = number(l.text)
            if None not in stats.values():
                break
        for key in ('level', 'atk', 'exp'):
            if stats[key] is None:
                a, b = MONSTER_STAT_COLUMNS[key]
                stats[key] = number(self.ocr.text(img, (a + 10, 300, b - a - 20, 40), 2, 'dark'))
        return stats

    def _first_text(self, img, rect, tries):
        """OCR is hit-and-miss on short words: return the first non-empty read."""
        for scale, mode in tries:
            text = self.ocr.text(img, rect, scale, mode).strip()
            if text:
                return text
        return ''

    def _monster_info(self, slot=0):
        img = self.shot()
        name = self._first_text(img, MONSTER_NAME_RECT, ((1.5, 'gray'), (1.0, 'gray'), (2, 'gray'), (1.5, 'dark')))
        if not name:
            name = re.sub(r'(?i)\s*card\s*$', '', self._first_text(img, MONSTER_CARD_RECT, ((2, 'gray'), (1.5, 'gray'))))
        tags = self.ocr.text(img, MONSTER_TAGS_RECT, scale=2)
        low = tags.lower()
        if 'big' in low and 'boss' in low:
            kind = 'Boss'
        elif 'mini' in low:
            kind = 'Mini-Boss'
        elif 'leader' in low or name.lower().startswith('leader'):
            kind = 'Leader'
        else:
            kind = 'Normal'
        el = re.search(r'(?i)ele\w*?ent\s*(\w+)', tags)
        element = max(ELEMENTS, key=lambda e: similar(e, el.group(1))) if el else ''
        spawn = self.ocr.text(img, MONSTER_SPAWN_RECT, scale=2)
        m = re.search(r'(\d+)\s*spawn', spawn)
        spawned = int(m.group(1)) if m else None
        m = re.search(r'respawn\s*(\d+)\s*(second|minute)', spawn)
        respawn = int(m.group(1)) * (60 if m.group(2) == 'minute' else 1) if m else None
        stats = self._monster_stats(img)
        if stats['level'] is None:          # lone 1-digit levels: read the "Lv.5" label on the thumbnail
            cx, _ = MONSTER_THUMBS[slot]
            for scale in (2, 3):
                m = re.search(r'[Ll][Vv]\.?\s*(\d+)', self.ocr.text(img, (cx + 5, 287, 52, 22), scale))
                if m:
                    stats['level'] = int(m.group(1))
                    break
        return {'name': name, 'kind': kind, 'element': element, 'spawned': spawned,
                'respawn_s': respawn, **stats}

    def _read_until_changed(self, read, old, timeout=3.0):
        """Call read() until its name differs from `old` (the UI lags behind taps)."""
        end = time.time() + timeout
        while True:
            value = read()
            name = value[0] if isinstance(value, tuple) else value['name']
            if (name and not same_name(name, old)) or time.time() > end:
                return value
            time.sleep(0.4)

    def scan_monsters(self):
        """Walk every area in Adventure Book -> Monsters and read each monster's info.
        The area pager wraps around, so it stops once an area comes up twice."""
        if not self.open_window(KEY_N, 'book', MENU_BOOK):
            return []
        self.tap(BOOK_TAB_MONSTERS, 1.5)
        areas = []
        previous = ''                       # last monster read, to spot a panel that has not updated yet
        name, lo, hi = self._monster_area()
        for _ in range(60):
            if any((lo, hi) == (a['min'], a['max']) and similar(name, a['name']) >= 0.8 for a in areas):
                break
            area = {'name': name, 'min': lo, 'max': hi, 'monsters': []}
            for i, xy in enumerate(MONSTER_THUMBS):
                self.tap(xy, 0.5)
                info = self._read_until_changed(lambda: self._monster_info(i), previous, 2.5)
                if not info['name'] or (i > 0 and same_name(info['name'], previous)):
                    break                   # no monster in this slot
                area['monsters'].append(info)
                previous = info['name']
            self.log(f'{name} Lv.{lo}-{hi}: ' + ', '.join(f"{m['name']} Lv.{m['level']}" for m in area['monsters']))
            areas.append(area)
            self.tap(MONSTER_NEXT, 0.3)
            name, lo, hi = self._read_until_changed(self._monster_area, name)
        self.to_hud()
        areas = [a for a in areas if 'allmonsters' not in norm(a['name'])]
        # the pager wraps; rotate so the list starts at the lowest area, like the Card Book list
        if areas:
            first = min(range(len(areas)), key=lambda i: areas[i]['min'] or 999)
            areas = areas[first:] + areas[:first]
        return areas
