"""BotLevel99: levels the current class in Hunting Together RPG on LDPlayer.

    python ui.py                      open the control panel in the browser (recommended)
    python bot.py run                 start the bot in this console
    python bot.py status              show what the bot sees right now
    python bot.py scan                read every area and monster from Adventure Book > Monsters
    python bot.py hunt "Fang Hyena"   press Hunt this monster on one monster, once
    python bot.py gear                press Auto Gear + Auto Card (+ dismantle if on), once
    python bot.py stats               spend stat points by this class's table in config.toml, once
    python bot.py bosses              farm world boss cards ([boss] in config.toml), hopping servers
"""
import argparse
import collections
import datetime
import itertools
import json
import os
import re
import sys
import threading
import time
import tomllib
import winsound

import cv2

from device import Device, DeviceError
from game import KEYS, Game, app_page, blank_white, find, note, screen_state, seen, similar, step, target_hp, target_kind
from ocr import Ocr
from servers import Servers

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, 'config.toml')
BOSSES_PATH = os.path.join(HERE, 'data', 'bosses.json')
MAX_DEATHS_HERE = 5                       # deaths to one boss in one server before hopping on
MONSTERS_PATH = os.path.join(HERE, 'data', 'monsters.json')
LOG_DIR = os.path.join(HERE, 'logs')

# recent log lines for the control panel: (id, 'HH:MM:SS', message)
LOG_LINES = collections.deque(maxlen=400)
_log_ids = itertools.count(1)
_log_lock = threading.Lock()


def log(msg):
    now = datetime.datetime.now()
    line = f'[{now:%H:%M:%S}] {msg}'
    print(line, flush=True)
    with _log_lock:
        LOG_LINES.append((next(_log_ids), f'{now:%H:%M:%S}', msg))
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, 'bot.log'), 'a', encoding='utf-8') as f:
            f.write(f'{now:%Y-%m-%d} {line}\n')


def save_screen(img, tag, keep=50):
    """Save a screenshot into logs/ for later checking, keeping only the newest `keep`."""
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f'{datetime.datetime.now():%Y%m%d-%H%M%S}-{tag}.png')
    cv2.imwrite(path, img)
    shots = sorted(f for f in os.listdir(LOG_DIR) if f.endswith('.png'))
    for old in shots[:-keep]:
        os.remove(os.path.join(LOG_DIR, old))
    return path


def load_dodges():
    """{boss: {skill: {how, steps}}} from data/bosses.json ({} when missing)."""
    try:
        with open(BOSSES_PATH, encoding='utf-8') as f:
            return {b: v['skills'] for b, v in json.load(f)['bosses'].items()}
    except (OSError, ValueError, KeyError):
        return {}


def load_config():
    with open(CONFIG_PATH, 'rb') as f:
        return tomllib.load(f)


def save_boss_targets(targets):
    """Rewrite `[boss] targets` in config.toml, keeping the line's comment."""
    with open(CONFIG_PATH, encoding='utf-8') as f:
        text = f.read()
    value = json.dumps(targets, ensure_ascii=False)
    text, n = re.subn(r'(?m)^(targets\s*=\s*)[^#\n]*?(\s*(#.*)?)$', lambda m: f'{m.group(1)}{value}{m.group(2)}', text)
    if n != 1:
        raise OSError('หา targets ใน config.toml ไม่เจอ')
    with open(CONFIG_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def load_areas():
    if not os.path.exists(MONSTERS_PATH):
        return []
    with open(MONSTERS_PATH, encoding='utf-8') as f:
        return json.load(f)['areas']


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        ld = cfg['ldplayer']
        self.dev = Device(ld['dir'], ld['serial'], ld['index'])
        self.ocr = Ocr()
        self.game = Game(self.dev, self.ocr, log)
        self.areas = load_areas()
        self.area_names = [a['name'] for a in self.areas]
        self.stop_event = threading.Event()
        # what the bot last saw and did, for the control panel
        self.status = {'screen': None, 'level': None, 'exp': None, 'target': None,
                       'started_at': None, 'start_level': None, 'finished': False,
                       'hunts': 0, 'rejoins': 0, 'restarts': 0,
                       'mode': None, 'kills': 0, 'deaths': 0, 'hops': 0, 'cards': [], 'boss_done': [],
                       'timed_hops': 0, 'timers': 0, 'next_due': None,
                       'boss_targets': [], 'targets_changed': 0}   # set when the bot takes a boss off the list
        place = re.search(r'placeId=(\d+)', cfg['bot']['join_url'])
        self.servers = Servers(place.group(1)) if place else None
        self.failures = 0           # rounds in a row where LDPlayer/adb did not answer or a rejoin failed
        self.restart_streak = 0     # LDPlayer restarts in a row that did not get back into the game
        self.stats_due = True       # spend stat points at the next chance (start, level-up, table saved)
        self.no_table_logged = None
        self.last_card_at = 0.0     # one "Obtained!" popup stays up ~5 s: count it once

    def wait(self, seconds):
        """Sleep, but wake up at once when the bot is asked to stop. True if stopping."""
        return self.stop_event.wait(seconds)

    def idle(self, seconds, press_e):
        """wait(), pressing E every press_e_every_s seconds meanwhile when `press_e`: E opens a
        Hard Worker Box the character stands next to while it farms. It walks past a box in
        about a second, so E goes several times a second (one press is one ~0.05 s adb call)."""
        every = float(self.cfg['bot'].get('press_e_every_s', 0.2))
        if not press_e or every <= 0:
            return self.wait(seconds)
        note('key', label=f'E ทุก {every:g} วิ')
        start = time.time()
        end, n = start + seconds, 0
        while time.time() < end:
            self.dev.key(KEYS['E'])
            n += 1
            if self.wait(max(0.0, min(start + n * every, end) - time.time())):
                return True
        return False

    # ---- plan -----------------------------------------------------------
    def find_monster(self, monster, area=None):
        """(area name, monster name) for a monster, using data/monsters.json when no area is given."""
        if area:
            return area, monster
        best = (0.0, None)
        for a in self.areas:
            for m in a['monsters']:
                score = similar(m['name'], monster)
                if score > best[0]:
                    best = (score, (a['name'], m['name']))
        return best[1] if best[0] >= 0.8 else None

    def card_order(self, area):
        """The area's monsters in data/monsters.json order, which is its cards' order in the Card Book."""
        return next(([m['name'] for m in a['monsters']] for a in self.areas if a['name'] == area), [])

    def target_for(self, level):
        for step in self.cfg.get('plan', []):
            if step['from'] <= level <= step['to']:
                return self.find_monster(step['monster'], step.get('area'))
        return None

    def stat_rows(self, job):
        """This class's stat table from config.toml [stats], lowest level first."""
        for name, rows in self.cfg.get('stats', {}).items():
            if job and name.lower() == job.lower():
                return sorted(rows, key=lambda r: r.get('level', 0))
        return None

    def raise_stats(self, level):
        """Spend stat points by the current class's table. Classes without one are left alone."""
        g = self.game
        job = g.job or g.read_job()
        if not job:
            log('อ่านชื่ออาชีพจากกระเป๋าไม่ได้ ข้ามการอัพ Stats รอบนี้')
            return False
        rows = self.stat_rows(job)
        if not rows:
            if self.no_table_logged != job:
                log(f'ยังไม่มีตารางอัพ Stats ของ {job} บอทจะไม่ยุ่งกับ Stats (เพิ่มได้ในแท็บตารางอัพ Stats)')
                self.no_table_logged = job
            return False
        return g.raise_stats(rows, level)

    # ---- recovery -------------------------------------------------------
    def popup_button(self, img):
        """Tap a known button on an unknown screen (Roblox's Reconnect, etc). True if tapped."""
        for line in self.ocr.read(img, scale=1.0):
            text = line.text.strip()
            if similar(text, 'Reconnect') >= 0.8 or similar(text, 'Retry') >= 0.8:
                log(f'กดปุ่ม "{text}"')
                self.game.tap(line.center, 3)
                return True
        return False

    def rejoin(self, why):
        log(f'{why} → เข้าเกมใหม่')
        step('ปิด Roblox แล้วเข้าเกมใหม่', why)
        self.status['rejoins'] += 1
        self.dev.stop_roblox()
        self.wait(3)
        return self._join(self.cfg['bot']['join_url'])

    def hop(self, why, boss=None):
        """Move to another server: one whose `boss` respawns about when we get there, or else one
        picked by the panel's rule (fewest players, random...) that we have not been to lately.

        [boss] direct_join (default on): open the new server's link from inside the game. Roblox
        then leaves the old server by itself, as a teleport does (the game saves on leaving either
        way), which skips the Leave menu and the app's home page. A server that does not take us
        (the link does nothing in game, or Roblox drops back to its home page) is swapped for
        another; after two such links in game the bot leaves through the Roblox menu first."""
        log(f'{why} → ย้ายห้อง')
        st = self.status
        st['hops'] += 1
        self.hop_started = time.time()
        self.timed = None
        self.deaths_here = 0
        direct = self.cfg.get('boss', {}).get('direct_join', True) and self.dev.roblox_in_front() \
            and not app_page(self.game.shot())
        if not direct:
            self._leave()
        ignored = 0
        for attempt in range(3):
            url = self._pick_url(boss)
            if direct:
                ok = self._join(url, from_game=True)
                if self.game.restart_wanted:
                    return False
                if ok is None:                  # still on the old server: this one is full or gone
                    ignored += 1
                    if ignored >= 2:
                        log('เปิดลิงก์ห้องใหม่จากในเกม 2 ห้องแล้วไม่ย้าย → ออกผ่านเมนูแล้วเข้าห้องอื่น')
                        direct = False
                        self._leave()
                    else:
                        log('เปิดลิงก์ห้องใหม่จากในเกมแล้วไม่ย้ายห้อง (ห้องอาจเต็มหรือปิดแล้ว) → ลองห้องอื่น')
                    continue
            else:
                ok = self._join(url, give_up_s=45)
            if ok or self.stop_event.is_set() or self.game.restart_wanted:
                return ok
            log('เข้าห้องนี้ไม่ได้ (Roblox เด้งกลับหน้าแอป) → ลองห้องอื่น')
            direct = False                      # from Roblox's own page now
        return False

    def _pick_url(self, boss):
        """The link of the next server to try (see hop), or the plain game link (a random server)."""
        st = self.status
        server = None
        step('ดึงรายชื่อห้อง')
        try:
            cfg = self.cfg.get('boss', {})
            if self.servers:
                self.servers.cooldown = cfg.get('server_cooldown_min', 12) * 60
                self.servers.min_players = cfg.get('server_min_players', 6)
                due = self.servers.due(boss) if boss and cfg.get('follow_timers', True) else None
                if due:
                    server, self.timed = due
                    st['timed_hops'] += 1
                    log(f'ไปห้องที่จำเวลาไว้: {boss} จะเกิดในอีก {self.timed:.0f} วิ '
                        f'(ย้ายห้องใช้ประมาณ {self.servers.travel:.0f} วิ)')
                else:
                    self.timed = None
                    skip = cfg.get('server_skip', [0, 9])
                    server = self.servers.pick(cfg.get('server_pick', 'fewest'), (min(skip), max(skip)))
                    if server and server.get('playing', 0) < self.servers.min_players:
                        log(f'ห้องที่มีคน {self.servers.min_players}+ เข้าไปครบหมดแล้ว (ติดคูลดาวน์) '
                            f'เลยเข้าห้องที่คนเยอะสุดในที่เหลือแทน')
        except (OSError, ValueError) as e:
            log(f'ดึงรายชื่อห้องไม่ได้ ({e}) เข้าห้องสุ่มแทน')
        if server:
            log(f'เข้าห้องที่มีคน {server["playing"]}/{server["maxPlayers"]} คน')
            step('เข้าห้องใหม่', f'{server["playing"]}/{server["maxPlayers"]} คน' + (' (ตามเวลา)' if self.timed else ''))
            return self.servers.join_url(server)
        if self.servers:
            self.servers.current = None         # a random server: its id is unknown
        return self.cfg['bot']['join_url']

    def _leave(self):
        """Leave through the Roblox menu (the game saves); force-stop Roblox when that fails."""
        if not self.game.leave():
            self.dev.stop_roblox()
            self.wait(3)

    def _join(self, url, from_game=False, give_up_s=None):
        """Open a roblox:// link and wait (up to 3 minutes) until the game is playable.
        from_game: the link is opened while still in a server. Until something other than that
        game's HUD shows, its HUD is not taken for the new server; None when nothing changes within
        12 s (Roblox did not act on the link). give_up_s: False once Roblox has sat on its own home
        page that long with the link sent again every 20 s (the server will not take us).

        As soon as a join has loaded the game shows a Skip button over its intro camera:
        pressing it is the earliest sign we are in, and skips the camera swing too. When boss
        farming the camera is then zoomed out, so a boss's respawn sign stays on screen."""
        step('รอโหลดเข้าเกม')
        g = self.game
        self.dev.open_url(url)
        sent = fixed = time.time()
        end = time.time() + 180
        img = hud_since = None
        gone = not from_game                    # has the old server's screen gone yet?
        on_app_since = white_since = None
        other_since = None                      # from_game: since when no HUD has shown
        while time.time() < end:
            if self.wait(0.5 if not gone else 1):
                return False
            try:
                if not self.dev.roblox_in_front():
                    gone = True
                    continue
                img = g.shot()
                if g.restart_wanted:
                    return False
                if blank_white(img):
                    white_since = white_since or time.time()
                    if time.time() - white_since > 15:
                        save_screen(img, 'join-white')
                        g.restart_wanted = 'เข้าห้องแล้วจอขาวค้าง 15 วิ'
                        return False
                    continue
                white_since = None
                if not gone:
                    # gone for sure: Roblox's own page, or the new server's Skip button; a card popup
                    # or a window of the old game also hides its HUD, but not for 6 s on end
                    if app_page(img) or find(img, 'skip_btn')[1]:
                        gone = True
                    elif screen_state(img) == 'hud':
                        other_since = None
                    else:
                        other_since = other_since or time.time()
                        gone = time.time() - other_since >= 6
                    if not gone:
                        if other_since is None and time.time() - sent > 15:
                            return None             # still on the old server's HUD
                        continue
                    log(f'ออกจากห้องเดิมแล้ว ({time.time() - sent:.1f} วิหลังเปิดลิงก์)')
                if app_page(img):
                    on_app_since = on_app_since or time.time()
                    if give_up_s and time.time() - on_app_since > give_up_s:
                        log(f'Roblox อยู่หน้าแอป {give_up_s} วิแล้วยังเข้าห้องนี้ไม่ได้')
                        return False
                    if time.time() - sent > 20:
                        # a cold start can drop the link and leave Roblox on its own home page
                        self.dev.open_url(url)
                        sent = time.time()
                    continue
                on_app_since = None
                skip = find(img, 'skip_btn')[1]
                if skip:
                    g.tap(skip, 1.0)
                elif screen_state(img) == 'hud':
                    # no Skip seen (missed it, or the game stopped showing one): let the intro finish
                    hud_since = hud_since or time.time()
                    if time.time() - hud_since < 4:
                        continue
                else:
                    hud_since = None
                    if time.time() - fixed > 5:     # a Reconnect button or a window over the game
                        fixed = time.time()
                        if not self.popup_button(img):
                            g.close_any()
                    continue
                log('เข้าเกมแล้ว')
                clicks = self.cfg.get('boss', {}).get('zoom_out_clicks', 10)
                if self.status.get('mode') == 'boss' and clicks:
                    g.zoom_out(clicks)
                return True
            except DeviceError:
                continue
        if img is not None:
            save_screen(img, 'rejoin-failed')
        log('เข้าเกมใหม่ไม่สำเร็จภายใน 3 นาที จะลองอีกรอบ')
        return False

    def restart_ldplayer(self, why=None):
        """Close and reopen LDPlayer after it stopped answering (or `why`), then get back into the game."""
        if self.restart_streak >= 3:
            log('เปิด LDPlayer ใหม่ 3 รอบแล้วยังเข้าเกมไม่ได้ จะรอ 10 นาทีแล้วลองอีกครั้ง')
            if self.wait(600):
                return
            self.restart_streak = 0
        self.restart_streak += 1
        self.status['restarts'] += 1
        log(f'{why or f"LDPlayer ไม่ตอบ {self.failures} รอบติดกัน"} → ปิดแล้วเปิด LDPlayer ใหม่')
        step('ปิดแล้วเปิด LDPlayer ใหม่')
        self.failures = 0
        if not self.dev.restart_emulator():
            log('เปิด LDPlayer ใหม่แล้ว แต่ Android ยังบูตไม่เสร็จใน 4 นาที')
            self.failures = self.cfg['bot'].get('restart_after_failures', 3) - 1
            return
        log('เปิด LDPlayer ใหม่แล้ว')
        if not self.rejoin('LDPlayer เพิ่งเปิดใหม่'):
            self.failures += 1

    def ensure_game(self):
        """LDPlayer up, Roblox in front and inside the game. Returns a fresh screenshot, or None
        when something had to be fixed first (the caller forgets what it was doing and goes again)."""
        st = self.status
        if self.game.restart_wanted:            # the status bar will not go, a join hung on white
            why, self.game.restart_wanted = self.game.restart_wanted, None
            self.restart_ldplayer(why)
            return None
        if self.failures >= self.cfg['bot'].get('restart_after_failures', 3):
            self.restart_ldplayer()
            return None
        if not self.dev.emulator_running():
            st['screen'] = 'offline'
            log('LDPlayer ปิดอยู่ กำลังเปิด')
            if not self.dev.start_emulator():
                self.failures += 1
                return None
        if not self.dev.responsive():
            st['screen'] = 'offline'
            self.failures += 1
            log(f'LDPlayer ไม่ตอบ (ครั้งที่ {self.failures})')
            self.wait(10)
            return None
        if not self.dev.roblox_in_front():
            st['screen'] = 'offline'
            if not self.rejoin('Roblox ไม่ได้อยู่หน้าจอ'):
                self.failures += 1
            return None
        img = self.game.shot()
        if app_page(img):
            st['screen'] = 'offline'
            if not self.rejoin('Roblox อยู่ที่หน้าแอป ไม่ได้อยู่ในเกม'):
                self.failures += 1
            return None
        return img

    # ---- main loop ------------------------------------------------------
    def run(self):
        g = self.game
        st = self.status
        level = pending = None
        target = None
        retarget = None                 # ((area, monster), when Hunt was pressed) until the hunt is stopped
        bar_seen_at = 0.0               # last time a monster's HP bar showed (E is pressed only at the farm)
        progress_key, progress_at = None, time.time()
        stalls = hunt_fails = 0
        last_gear, gear_due = 0.0, True
        unknown_since = None
        no_plan_logged = None
        no_level_since, no_level_logged = None, False
        st.update(started_at=time.time(), start_level=None, finished=False, target=None, mode='level')
        self.stats_due, self.no_table_logged = True, None
        self.stop_event.clear()
        log('เริ่มบอท')
        while not self.stop_event.is_set():
            bot = self.cfg['bot']               # re-read each round: the control panel can change it
            try:
                img = self.ensure_game()
                if img is None:
                    target = st['target'] = None
                    continue
                now = time.time()
                s = st['screen'] = screen_state(img)
                if s != 'unknown':
                    self.failures = self.restart_streak = 0
                if s in ('book', 'bag', 'menu'):
                    g.to_hud()
                    continue
                if s == 'unknown':
                    unknown_since = unknown_since or now
                    if not self.popup_button(img) and now - unknown_since > bot['unknown_timeout_s']:
                        log(f'ไม่รู้จักหน้าจอนี้ ({save_screen(img, "unknown")})')
                        if not g.close_any():
                            if not self.rejoin('ติดหน้าจอแปลก'):
                                self.failures += 1
                            target = st['target'] = None
                        unknown_since = None
                    self.wait(2)
                    continue
                unknown_since = None

                lv, exp = g.level(level)
                # a new level must be read twice in a row before the bot acts on it
                if lv is not None and lv != level:
                    if lv == pending:
                        if level is not None and lv > level:
                            gear_due = self.stats_due = True
                        log(f'เลเวล {level} → {lv}' if level else f'เลเวลปัจจุบัน {lv}')
                        level, pending = lv, None
                    else:
                        pending = lv
                elif lv == level:
                    pending = None
                if level is None:
                    no_level_since = no_level_since or now
                    if not no_level_logged and now - no_level_since > 30:
                        log(f'อ่านเลเวลมุมซ้ายล่างไม่ได้ ({save_screen(img, "no-level")})')
                        no_level_logged = True
                    self.wait(2)
                    continue
                no_level_since, no_level_logged = None, False
                st['level'], st['exp'] = level, exp
                if st['start_level'] is None:
                    st['start_level'] = level
                key = (level, round(exp, 3))
                if key != progress_key:
                    progress_key, progress_at, stalls = key, now, 0

                if level >= bot['stop_level']:
                    if self.stats_due:          # spend the last level's points before stopping
                        self.stats_due = False
                        self.raise_stats(level)
                    log(f'ถึงเลเวล {level} แล้ว หยุดบอท')
                    st['finished'] = True
                    for _ in range(3):
                        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                        time.sleep(0.6)
                    break

                want = self.target_for(level)
                if want is None:
                    if no_plan_logged != level:
                        log(f'ไม่มีแผนสำหรับเลเวล {level} (ตั้งในแผนการตี) หรือยังไม่ได้สแกนข้อมูลมอน')
                        no_plan_logged = level
                    self.wait(bot['check_every_s'])
                    continue
                if want != target:
                    log(f'ไปตี {want[1]} ที่ {want[0]}')
                    if g.hunt(*want, known_areas=self.area_names, order=self.card_order(want[0])):
                        target, progress_at, hunt_fails = want, time.time(), 0
                        st['target'] = list(want)
                        st['hunts'] += 1
                        retarget = (want, time.time()) if bot.get('auto_all_after_hunt', True) else None
                        bar_seen_at = 0.0       # the walk there can pass through town
                    else:
                        hunt_fails += 1
                        log(f'กด Hunt ไม่สำเร็จ (ครั้งที่ {hunt_fails}) {save_screen(g.shot(), "hunt-failed")}')
                        if hunt_fails >= 3:
                            if not self.rejoin('กด Hunt ไม่สำเร็จหลายรอบ'):
                                self.failures += 1
                            hunt_fails = 0
                        self.wait(5)
                    continue

                if now - progress_at > bot['stall_minutes'] * 60:
                    stalls += 1
                    log(f'EXP ไม่ขยับมา {bot["stall_minutes"]} นาที (ครั้งที่ {stalls}) {save_screen(img, "stall")}')
                    if stalls >= 3:
                        if not self.rejoin('ค้างหลายรอบ'):
                            self.failures += 1
                        stalls = 0
                    target, progress_at = None, time.time()
                    continue

                # Hunt this monster makes Auto go for that one monster only; once we are there,
                # Stop in the Card Book and Auto off and on again (F twice) make it fight every
                # monster around (F alone is not enough: the user checked)
                if retarget:
                    (area, name), since = retarget
                    if g.fighting(name):
                        retarget = None
                        step('กด Stop แล้วปิดเปิด Auto', name)
                        stopped = g.stop_hunt(area, name, known_areas=self.area_names)
                        toggled = g.auto_all()
                        if stopped and toggled:
                            log(f'ถึงที่ฟาร์มแล้ว กด Stop ที่การ์ด {name} แล้วปิดแล้วเปิด Auto ใหม่ (F 2 ครั้ง) ให้ตีทุกตัวแถวนั้น')
                        else:
                            log(('กด Stop ไม่สำเร็จ ' if not stopped else '') + ('ปิดแล้วเปิด Auto ไม่สำเร็จ ' if not toggled else '')
                                + f'({save_screen(g.shot(), "auto-toggle")})')
                        continue
                    if now - since > 180:
                        log(f'กด Hunt แล้ว 3 นาทียังไม่เห็นว่ากำลังตี {name} เลยไม่ได้กด Stop และปิดแล้วเปิด Auto')
                        retarget = None

                if gear_due or now - last_gear > bot['auto_gear_minutes'] * 60:
                    log('กด Auto Gear + Auto Card' + (' แล้วย่อยของ' if bot['dismantle'] else ''))
                    g.auto_equip(bot['dismantle'])
                    last_gear, gear_due = time.time(), False
                    continue

                # after Auto Gear, which reads the class name from the Bag
                if self.stats_due:
                    self.stats_due = False
                    self.raise_stats(level)
                    continue

                step('ตีมอนอยู่', want[1])
                # E only at the farm, i.e. a monster's HP bar showed within the last minute (it can
                # be gone for a moment between kills): in town it could open an NPC
                if target_hp(img) > 0:
                    bar_seen_at = now
                self.idle(3 if retarget else bot['check_every_s'], now - bar_seen_at < 60)
            except DeviceError as e:
                self.failures += 1
                log(f'adb มีปัญหา (ครั้งที่ {self.failures}): {e}')
                self.wait(10)
        step('หยุดบอท')
        log('หยุดบอทแล้ว')


    # ---- boss card farm ---------------------------------------------------
    def run_bosses(self):
        """Hunt the first boss of `[boss] targets`: kill it when it is up, hop to another server
        when it is down. With until_card on, a card that drops while hunting takes that boss off
        the list (config.toml too) and the next one is hunted; a boss whose card was owned before
        is hunted like any other. With it off the first boss is hunted for good."""
        g, st = self.game, self.status
        st.update(started_at=time.time(), finished=False, target=None, mode='boss', boss_done=[], deaths=0)
        self.stop_event.clear()
        self.deaths_here = 0
        skip = set()                    # names the bot cannot find in the monster list
        owned_at = {}                   # boss -> its card's "Owned" the first time the book showed it this run
        hunt_fails = lost = 0
        near = None                     # boss whose room we start next to (a hop puts you back where you were)
        dismantled_at = time.time()     # [boss] dismantle_every_min counts from the start
        self.hop_started = self.timed = None
        log('เริ่มฟาร์มบอส')
        while not self.stop_event.is_set():
            cfg = self.cfg.get('boss', {})      # re-read each round: the control panel can change it
            until = cfg.get('until_card', True)
            todo = [b for b in cfg.get('targets', []) if b not in skip]
            if not todo:
                log('ได้การ์ดบอสครบทุกตัวที่เลือกแล้ว' if st['boss_done'] else 'ยังไม่ได้เลือกบอสที่จะฟาร์ม (แท็บฟาร์มบอส)')
                st['finished'] = bool(st['boss_done'])
                break
            try:
                img = self.ensure_game()
                if img is None:
                    st['target'] = None
                    continue
                s = st['screen'] = screen_state(img)
                if s == 'unknown':
                    if not self.popup_button(img) and not g.close_any():
                        self.wait(3)
                    continue
                self.failures = self.restart_streak = 0
                every = cfg.get('dismantle_every_min', 0)
                if every and time.time() - dismantled_at >= every * 60:
                    # Auto Gear first (unless switched off), so a better piece is worn before the rest goes;
                    # the cards stay as picked
                    gear = cfg.get('dismantle_auto_gear', True)
                    log(f'ครบ {every:g} นาที → ' + ('กด Auto Gear แล้วย่อยของ' if gear else 'ย่อยของ (ไม่กด Auto Gear)'))
                    dismantled_at = time.time()
                    g.auto_equip(dismantle=True, cards=False, gear=gear)
                    continue                    # look at the screen afresh before going on
                want = self.find_monster(todo[0])
                if not want:
                    log(f'ไม่รู้จักบอส "{todo[0]}" ข้ามไป')
                    skip.add(todo[0])
                    continue
                if st['target'] != list(want):
                    log(f'ไปตีบอส {want[1]} ที่ {want[0]}')
                if not g.hunt(*want, known_areas=self.area_names, order=self.card_order(want[0])):
                    hunt_fails += 1
                    if hunt_fails >= 3:
                        hunt_fails = 0
                        if not self.hop('กด Hunt บอสไม่สำเร็จหลายรอบ', want[1]):
                            self.failures += 1
                    continue
                hunt_fails = 0
                st['target'] = list(want)
                st['hunts'] += 1
                if not until:
                    owned_at.pop(want[1], None)
                elif g.owned is not None:
                    # more copies than when this run first looked: a drop whose popup was missed
                    # (it can come up after the hop). A card owned from before does not count.
                    before = owned_at.setdefault(want[1], g.owned)
                    if g.owned > before:
                        log(f'การ์ด {want[1]} ในสมุดเพิ่มจาก {before} เป็น {g.owned} ใบ (ป้าย Obtained! อาจหลุดไป)')
                        self.drop_boss(want[1])
                        owned_at.pop(want[1], None)
                        continue
                if self.hop_started and self.servers:   # leave -> watching: how long a hop really takes
                    self.servers.note_travel(time.time() - self.hop_started)
                    self.hop_started = None
                # from the room's door the HP bar shows within a few seconds; from elsewhere it is a walk
                limit = cfg.get('appear_s', 6) if near == want[1] else 90
                wait_s = cfg.get('wait_respawn_s', 0)
                if self.timed is not None:              # came here for its respawn: wait for it
                    wait_s = max(wait_s, cfg.get('timed_wait_s', 40))
                result, in_room = self.watch_boss(want[1], wait_s, limit, cfg.get('after_kill_s', 2))
                self.update_schedule(want[1])
                if in_room:
                    near, lost = want[1], 0
                if result == 'lost':
                    lost += 1
                    if near != want[1]:
                        # a long wait is plenty to walk there: the boss is just down (its timer can be
                        # off screen), and the next server starts at this room's door
                        near, lost = want[1], 0
                    elif lost >= 5:  # five quick misses in a row: maybe not at the door any more
                        near, lost = None, 0
                else:
                    lost = 0
                if result == 'died':
                    self.deaths_here += 1
                    near = None                 # back in town: a long walk to the room
                    if self.deaths_here < MAX_DEATHS_HERE:
                        continue                # the boss is still up here: go again
                if result == 'card' and until:
                    self.drop_boss(want[1])
                    owned_at.pop(want[1], None)
                    continue                    # the next boss may be up on this server: no hop
                why = {'killed': f'ฆ่า {want[1]} แล้ว', 'card': f'ได้การ์ด {want[1]} แล้ว',
                       'dead': f'{want[1]} ยังไม่เกิด', 'lost': f'หา {want[1]} ไม่เจอ',
                       'died': f'ตายกับ {want[1]} {MAX_DEATHS_HERE} รอบในห้องนี้'}[result]
                if not self.stop_event.is_set() and not self.hop(why, want[1]):
                    self.failures += 1
            except DeviceError as e:
                self.failures += 1
                log(f'adb มีปัญหา (ครั้งที่ {self.failures}): {e}')
                self.wait(10)
        step('หยุดบอท')
        log('หยุดฟาร์มบอสแล้ว')

    def watch_boss(self, boss, wait_s, limit, after_kill):
        """After pressing Hunt on a boss, watch the screen twice a second until it is time
        to move on. Returns (result, saw the room) where result is 'killed' (it died while we
        were here), 'card' (and its card dropped), 'dead' (its respawn timer is over wait_s),
        'lost' (no HP bar or timer within `limit` seconds) or 'died' (we were killed and are
        back in town). After a kill it keeps looking for an "Obtained!" popup for `after_kill`
        seconds. A boss listed in data/bosses.json is watched as fast as screenshots allow,
        and each skill it announces is dodged on that skill's timing."""
        g, st = self.game, self.status
        start, fought, got, sign_at = time.time(), None, False, None
        dodges = load_dodges().get(boss, {})     # re-read each fight so data/bosses.json can be tuned
        plan, last_warn = [], (None, 0.0)        # plan: [(when, step)]
        took = 0.6                               # how long one screen check takes
        checks, check_s = 0, 0.0

        def run_due():
            # a dodge that falls due before the next check would end goes now, on time
            while plan and plan[0][0] - time.time() < took:
                if self.wait(max(0.0, plan[0][0] - time.time())):
                    return
                s = plan.pop(0)[1]
                if 'hold' in s:
                    g.hold(s['hold'], s.get('for', 1.0))
                else:
                    g.press(s['key'], s.get('times', 1))

        step('ดูห้องบอส', boss)
        while not self.stop_event.is_set():
            run_due()
            begin = time.time()
            img = g.shot()
            taken = (begin + time.time()) / 2            # dodges are timed from when the screen was seen
            if dodges:
                skill = g.boss_warning(img, dodges)
                # one warning stays up for a few seconds: act on its first sighting only
                if skill and (skill != last_warn[0] or taken - last_warn[1] > 4):
                    log(f'{boss} ใช้ {skill} → {dodges[skill].get("how", "หลบ")}')
                    plan = sorted(plan + [(taken + s['at'], s) for s in dodges[skill]['steps']],
                                  key=lambda p: p[0])
                    run_due()                            # e.g. Flame Breath: start walking at once
                if skill:
                    last_warn = (skill, taken)
            if seen(img, 'you_died', 0.8):              # the badge goes when we die too: not a kill
                return self.died(boss), True
            card = g.card_popup(img)
            if card is not None:
                got |= self.card_dropped(card, boss)
            if target_kind(img) == 'boss':
                fought = time.time()
            else:
                left = g.boss_respawn(img)
                # the badge went away: a strong class kills a boss in seconds, and its respawn
                # sign is often off screen (the camera faces the door), so do not wait for the sign
                if fought and (left is not None or time.time() - fought > 4):
                    st['kills'] += 1
                    log(f'ฆ่า {boss} แล้ว (รวม {st["kills"]} ตัว)')
                    if self.servers:                    # a world boss is back 10 minutes after it dies
                        self.servers.note_respawn(boss, left if left is not None else 600 - (time.time() - fought))
                    step('รอดูการ์ด', boss)
                    got |= self.wait_card(boss, after_kill)
                    return ('card' if got else 'killed'), True
                if left is not None and self.servers:
                    self.servers.note_respawn(boss, left)
                if left is not None and left > wait_s:
                    log(f'{boss} ตายอยู่ จะเกิดอีก {left // 60}:{left % 60:02d}')
                    return 'dead', True
                if left is not None and not fought:
                    if sign_at is None:
                        log(f'{boss} จะเกิดอีก {left} วิ รออยู่ในห้อง')
                        step('รอบอสเกิด', boss)
                    sign_at = time.time()
                # after the sign goes the boss still needs a moment to appear and be targeted
                since, allowed = (sign_at, max(limit, 8)) if sign_at else (start, limit)
                if left is None and not fought and time.time() - since > allowed:
                    log(f'กด Hunt แล้ว {limit} วินาทียังไม่เห็นแถบเลือด {boss}' if not sign_at
                        else f'{boss} น่าจะเกิดแล้วแต่ไม่เห็นแถบเลือด')
                    return 'lost', bool(sign_at)
            took = time.time() - begin
            checks += 1
            check_s += took
            if dodges and checks == 20:              # a warning is readable for only 0.8-1.9 s
                log(f'เช็คจอ 20 รอบแรก เฉลี่ย {check_s / 20:.2f} วิ/รอบ (ควรต่ำกว่า 0.4)')
            self.wait(0.1 if dodges else 0.5)
        return ('card' if got else 'lost'), bool(fought)

    def died(self, boss):
        """The "You Died" window is up: respawn in town for free. Returns 'died'."""
        st = self.status
        st['deaths'] = st.get('deaths', 0) + 1
        log(f'ตายกับ {boss} (รวม {st["deaths"]} ครั้ง) → เกิดใหม่ในเมือง (ฟรี)')
        step('ตาย เกิดใหม่ในเมือง', boss)
        for _ in range(5):
            img = self.game.shot()
            if not seen(img, 'you_died', 0.8):
                break
            if not self.game.respawn_in_town(img):
                self.wait(1)                    # it respawns in town by itself after 5:00 anyway
        return 'died'

    def update_schedule(self, boss):
        """For the panel: how many servers have a known respawn of `boss`, and the soonest."""
        times = self.servers.schedule(boss) if self.servers else []
        self.status['timers'] = len(times)
        self.status['next_due'] = round(times[0]) if times else None

    def wait_card(self, boss, seconds):
        got = False
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            card = self.game.card_popup(self.game.shot())
            if card is not None:
                got |= self.card_dropped(card, boss)
            self.wait(1)
        return got

    def drop_boss(self, boss):
        """[boss] until_card: `boss`'s card dropped, so take it off the list (in config.toml too,
        so the panel shows it unpicked and a restart does not go back to it); picking it
        again in the panel hunts it again."""
        st = self.status
        if boss not in st['boss_done']:
            st['boss_done'] = st['boss_done'] + [boss]
        cfg = self.cfg.setdefault('boss', {})
        left = [b for b in cfg.get('targets', []) if b != boss]
        cfg['targets'] = left                   # the next round goes on with the rest even if saving fails
        st['boss_targets'] = list(left)
        st['targets_changed'] += 1
        try:
            save_boss_targets(left)
        except OSError as e:
            log(f'เอา {boss} ออกจากรายการใน config.toml ไม่สำเร็จ: {e}')
        log(f'ได้การ์ด {boss} แล้ว → เอาออกจากรายการ' + (f' ไปตีบอส {left[0]} ต่อ' if left else ' ไม่เหลือบอสในรายการแล้ว'))
        self.timed = None                       # the server was picked for this boss's respawn, not the next one's

    def card_dropped(self, name, boss):
        """Note an "Obtained!" popup (once per popup). True when it is this boss's card."""
        if time.time() - self.last_card_at < 8:
            return False
        self.last_card_at = time.time()
        mine = similar(name, boss) >= 0.8
        self.status['cards'].append([datetime.datetime.now().strftime('%H:%M:%S'), name or '?', mine])
        shot = save_screen(self.game.img, 'card')
        log(f'ได้การ์ด {name or "(อ่านชื่อไม่ออก)"}' + (' ← การ์ดบอสที่ฟาร์มอยู่' if mine else '') + f' ({shot})')
        return mine


def cmd_status(bot):
    g = bot.game
    print('Roblox อยู่หน้าจอ:', bot.dev.roblox_in_front())
    img = g.shot()
    s = screen_state(img)
    print('หน้าจอ:', s)
    if s == 'hud':
        lv, exp = g.level()
        print(f'เลเวล: {lv}  EXP: {exp:.1%}')
        want = bot.target_for(lv) if lv else None
        print('แผนตอนนี้:', f'{want[1]} ที่ {want[0]}' if want else '-')


def save_monsters(areas):
    os.makedirs(os.path.dirname(MONSTERS_PATH), exist_ok=True)
    with open(MONSTERS_PATH, 'w', encoding='utf-8') as f:
        json.dump({'scanned': datetime.datetime.now().isoformat(timespec='seconds'), 'areas': areas},
                  f, ensure_ascii=False, indent=1)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['run', 'status', 'scan', 'hunt', 'gear', 'stats', 'bosses'])
    p.add_argument('monster', nargs='?')
    args = p.parse_args()
    bot = Bot(load_config())
    try:
        if args.command == 'run':
            bot.run()
        elif args.command == 'bosses':
            bot.run_bosses()
        elif args.command == 'status':
            cmd_status(bot)
        elif args.command == 'scan':
            areas = bot.game.scan_monsters()
            if areas:
                save_monsters(areas)
                print(f'บันทึก {len(areas)} แมพลง {MONSTERS_PATH}')
            else:
                print('สแกนไม่สำเร็จ')
        elif args.command == 'hunt':
            want = bot.find_monster(args.monster or '')
            if not want:
                print('ไม่รู้จักมอนตัวนี้ (ลองรัน scan ก่อน)')
                return
            print('ผล:', bot.game.hunt(*want, known_areas=bot.area_names))
        elif args.command == 'gear':
            print('ผล:', bot.game.auto_equip(bot.cfg['bot']['dismantle']))
        elif args.command == 'stats':
            bot.game.to_hud()
            print('ผล:', bot.raise_stats(bot.game.level(None, bot.game.shot())[0]))
    except KeyboardInterrupt:
        bot.stop_event.set()
        log('หยุดบอทแล้ว')
    finally:
        bot.ocr.close()


if __name__ == '__main__':
    main()
