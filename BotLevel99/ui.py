"""BotLevel99 control panel: a local web page to start/stop the bot, watch it,
and edit the hunting plan.

    python ui.py        opens http://127.0.0.1:8765 in the browser

Only listens on 127.0.0.1, so nothing outside this computer can reach it.
"""
import http.server
import json
import mimetypes
import os
import re
import sys
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlparse

import cv2

import buildsim
from bot import (CONFIG_PATH, LOG_LINES, MONSTERS_PATH, Bot, load_areas, load_config, log,
                 save_monsters)
from device import DeviceError
from game import STAT_NAMES, STEPS, TRACE, screen_state

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, 'ui')
DATA_DIR = os.path.join(HERE, 'data')
CLASSES = ('Swordsman', 'Archer', 'Mage', 'Thief', 'Merchant', 'Cleric', 'Alchemist', 'Samurai')
HOST, PORT = '127.0.0.1', 8765
SETTINGS = {  # [bot] keys the panel may change: key -> (type, min, max)
    'stop_level': (int, 1, 200),
    'auto_gear_minutes': (int, 1, 240),
    'stall_minutes': (int, 1, 60),
    'dismantle': (bool, None, None),
    'auto_all_after_hunt': (bool, None, None),
    'press_e_every_s': (float, 0, 10),
}


class Panel:
    def __init__(self):
        self.bot = Bot(load_config())
        self.thread = None
        self.task = None                # one-off action running while the bot is stopped
        self.lock = threading.Lock()
        self.view_lock = threading.Lock()
        self.preview = (None, 0.0, 0.0) # (jpeg bytes, when the screenshot was taken, when encoded)
        self.idle_view = {}             # screen/level read by the preview while the bot is stopped

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    # ---- bot control ------------------------------------------------------
    def start(self, mode=None):
        """Start the mode chosen in the panel ([bot] mode): the level bot, or the boss card farm."""
        mode = mode or self.bot.cfg['bot'].get('mode', 'level')
        with self.lock:
            if self.running or self.task:
                return False
            work = self.bot.run_bosses if mode == 'boss' else self.bot.run
            self.thread = threading.Thread(target=self._run, args=(work,), daemon=True)
            self.thread.start()
            return True

    def _run(self, work):
        try:
            work()
        except Exception as e:          # keep the panel alive and say why the bot stopped
            log(f'บอทหยุดเพราะเกิดข้อผิดพลาด: {e!r}')

    def stop(self):
        self.bot.stop_event.set()

    def run_task(self, name, fn):
        """Run a one-off action (gear, hunt, scan) in the background while the bot is stopped."""
        with self.lock:
            if self.running or self.task:
                return False
            self.task = name

        def work():
            try:
                fn()
            except DeviceError as e:
                log(f'adb มีปัญหา: {e}')
            except Exception as e:
                log(f'ทำงาน "{name}" ไม่สำเร็จ: {e!r}')
            finally:
                self.task = None

        threading.Thread(target=work, daemon=True).start()
        return True

    def gear(self):
        log('กด Auto Gear + Auto Card' + (' แล้วย่อยของ' if self.bot.cfg['bot']['dismantle'] else ''))
        self.bot.game.auto_equip(self.bot.cfg['bot']['dismantle'])

    def hunt(self, monster):
        want = self.bot.find_monster(monster)
        if not want:
            log(f'ไม่รู้จักมอน "{monster}"')
            return
        log(f'ไปตี {want[1]} ที่ {want[0]}')
        if self.bot.game.hunt(*want, known_areas=self.bot.area_names):
            self.bot.status['target'] = list(want)

    def raise_stats(self):
        g = self.bot.game
        if not g.to_hud():
            log('ไม่ได้อยู่หน้าเกมปกติ อัพ Stats ไม่ได้')
            return
        if self.bot.raise_stats(g.level(self.idle_view.get('level'), g.shot())[0]) and g.stats:
            log('Stats ตอนนี้: ' + ' · '.join(f'{s} {g.stats[s]}' for s in STAT_NAMES))

    def read_cards(self):
        """Re-read how many of each card we own (Card Book) into data/cards.json."""
        data = load_data('cards.json')
        log('เริ่มอ่านการ์ดที่มีจาก Card Book')
        areas = [(a['area'], [c['monster'] for c in a['cards']]) for a in data['areas']]
        owned = self.bot.game.scan_card_owned(areas, self.bot.area_names)
        if not owned:
            log('อ่านการ์ดไม่สำเร็จ')
            return
        new = []
        for card in (c for a in data['areas'] for c in a['cards']):
            if card['monster'] in owned:
                if owned[card['monster']] and not card.get('owned'):
                    new.append(card['monster'])
                card['owned'] = owned[card['monster']]
        data['owned_read'] = time.strftime('%Y-%m-%d %H:%M')
        with open(os.path.join(DATA_DIR, 'cards.json'), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        total = sum(len(a['cards']) for a in data['areas'])
        have = sum(1 for a in data['areas'] for c in a['cards'] if c.get('owned'))
        log(f'อ่านการ์ดแล้ว {len(owned)}/{total} ใบ มีอยู่ {have} ใบ' + (f' ใหม่: {", ".join(new)}' if new else ''))

    def scan(self):
        log('เริ่มสแกนข้อมูลมอนจาก Adventure Book')
        areas = self.bot.game.scan_monsters()
        if not areas:
            log('สแกนไม่สำเร็จ')
            return
        save_monsters(areas)
        self.bot.areas = load_areas()
        self.bot.area_names = [a['name'] for a in self.bot.areas]
        log(f'สแกนเสร็จ ได้ {len(areas)} แมพ')

    # ---- what the panel shows ---------------------------------------------
    def preview_jpeg(self):
        """(jpeg bytes, when the screenshot was taken): a small screenshot of the emulator.
        While the bot works this is the bot's own latest screenshot, so the overlay's boxes
        line up with exactly what it looked at, and adb gets no extra work."""
        with self.view_lock:
            data, shot_at, made = self.preview
            if data and time.time() - made < 0.6:
                return data, shot_at
            g = self.bot.game
            busy = self.running or self.task
            img, at = (g.img, g.img_at) if busy and g.img is not None and time.time() - g.img_at < 2 else (None, 0)
            if img is None:
                try:
                    img, at = self.bot.dev.screenshot(), time.time()
                except DeviceError:
                    return data, shot_at
                if not busy:
                    self._read_idle(img)
            small = cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 78])
            if ok:
                self.preview = (buf.tobytes(), at, time.time())
            return self.preview[0], self.preview[1]

    def _read_idle(self, img):
        view = {'screen': screen_state(img), 'at': time.time()}
        # level_at is None until the HUD has been seen once (the panel can start on a window)
        if view['screen'] == 'hud' and time.time() - (self.idle_view.get('level_at') or 0) > 4:
            level, exp = self.bot.game.level(self.idle_view.get('level'), img)
            view.update(level=level, exp=exp, level_at=time.time())
        else:
            view.update({k: self.idle_view.get(k) for k in ('level', 'exp', 'level_at')})
        self.idle_view = view

    def state(self, after=0):
        st = dict(self.bot.status)
        st['dismantled'] = self.bot.game.dismantled
        st['job'] = self.bot.game.job
        st['stats'] = self.bot.game.stats
        if not self.running:
            for key in ('screen', 'level', 'exp'):
                if self.idle_view.get(key) is not None:
                    st[key] = self.idle_view[key]
        level = st.get('level')
        st['plan_target'] = list(self.bot.target_for(level) or []) if level else []
        return {
            'running': self.running,
            'stopping': self.running and self.bot.stop_event.is_set(),
            'task': self.task,
            'status': st,
            'now': time.time(),
            'logs': [list(line) for line in list(LOG_LINES) if line[0] > after],
            'steps': list(STEPS)[-150:],
        }

    @staticmethod
    def trace(after=0):
        return {'now': time.time(), 'trace': [t for t in list(TRACE) if t['id'] > after][-200:]}

    def config(self):
        cfg = self.bot.cfg
        return {
            'plan': cfg.get('plan', []),
            'settings': {k: cfg['bot'].get(k) for k in SETTINGS},
            'mode': cfg['bot'].get('mode', 'level'),
            'areas': self.bot.areas,
            'stats': cfg.get('stats', {}),
            'stat_names': STAT_NAMES,
            'boss': cfg.get('boss', {}),
            'bosses': [{'name': m['name'], 'area': a['name'], 'level': m.get('level')}
                       for a in self.bot.areas for m in a['monsters'] if m.get('kind') == 'Boss'],
            'build': cfg.get('build', {}),
            'builds': load_data('builds.json'),
            'cards': load_data('cards.json'),
            'extras': load_data('extras.json'),
            'armor_sets': load_data('sets.json'),
            'classes': CLASSES,
            'autobuild_classes': list(buildsim.CLASSES),
            'potions': [buildsim.NO_POTION, *buildsim.POTIONS],
        }

    # ---- saving config.toml (keeps the comments in the file) ---------------
    def save_plan(self, plan):
        rows = []
        for i, step in enumerate(plan, 1):
            try:
                lo, hi = int(step['from']), int(step['to'])
            except (KeyError, TypeError, ValueError):
                return f'แถวที่ {i}: ใส่เลเวลเป็นตัวเลข'
            monster = str(step.get('monster', '')).strip()
            if not 1 <= lo <= hi <= 200:
                return f'แถวที่ {i}: เลเวลเริ่มต้องไม่มากกว่าเลเวลจบ (1–200)'
            if not self.bot.find_monster(monster):
                return f'แถวที่ {i}: ไม่รู้จักมอน "{monster}"'
            rows.append((lo, hi, monster))
        body = '\n'.join(f'  {{ from = {f"{lo},":<4}to = {f"{hi},":<4}monster = {json.dumps(m)} }},'
                         for lo, hi, m in rows)
        text = open(CONFIG_PATH, encoding='utf-8').read()
        text, n = re.subn(r'(?ms)^plan = \[.*?^\]', lambda _: f'plan = [\n{body}\n]', text)
        if n != 1:
            return 'หาแผนการตีใน config.toml ไม่เจอ'
        self._write_config(text)
        log(f'บันทึกแผนการตีแล้ว ({len(rows)} ช่วง)')
        return None

    def save_stats(self, job, rows):
        """Save one class's stat table (no rows = delete it) into the [stats] section."""
        job = re.sub(r'\s+', ' ', str(job or '')).strip()
        if not re.fullmatch(r'[A-Za-z][A-Za-z \-]*', job):
            return 'ใส่ชื่ออาชีพเป็นภาษาอังกฤษตามที่เขียนในกระเป๋า เช่น Merchant'
        clean = []
        for i, row in enumerate(rows, 1):
            try:
                level = int(row.get('level'))
                values = {s: int(row[s]) for s in STAT_NAMES if row.get(s) not in (None, '')}
            except (TypeError, ValueError):
                return f'แถวที่ {i}: ใส่เป็นตัวเลข'
            if not 1 <= level <= 200:
                return f'แถวที่ {i}: เลเวลต้องอยู่ระหว่าง 1–200'
            if not values:
                return f'แถวที่ {i}: ใส่ค่าอย่างน้อย 1 stat'
            if any(not 1 <= v <= 200 for v in values.values()):
                return f'แถวที่ {i}: ค่า Stats ต้องอยู่ระหว่าง 1–200'
            clean.append({'level': level, **values})
        tables = {k: v for k, v in self.bot.cfg.get('stats', {}).items() if k.lower() != job.lower()}
        if clean:
            tables[job] = sorted(clean, key=lambda r: r['level'])
        lines = ['[stats]']
        for name, table in tables.items():
            key = name if re.fullmatch(r'[A-Za-z0-9_-]+', name) else json.dumps(name)
            lines.append(f'{key} = [')
            lines += ['  { ' + ', '.join(f'{k} = {v}' for k, v in r.items()) + ' },' for r in table]
            lines.append(']')
        section = '\n'.join(lines) + '\n'
        text = open(CONFIG_PATH, encoding='utf-8').read()
        # up to the next section, leaving the blank lines and comments that head it
        text, n = re.subn(r'(?ms)^\[stats\]\n.*?(?=^(?:[ \t]*(?:#[^\n]*)?\n)*\[|\Z)', lambda _: section, text)
        if n != 1:
            text = text.rstrip('\n') + '\n\n' + section
        self._write_config(text)
        self.bot.stats_due = True           # a running bot applies the new table on its next round
        log(f'บันทึกตารางอัพ Stats ของ {job} แล้ว ({len(clean)} แถว)' if clean else f'ลบตารางอัพ Stats ของ {job} แล้ว')
        return None

    def save_mode(self, mode):
        if mode not in ('level', 'boss'):
            return 'โหมดต้องเป็น level หรือ boss'
        if self.running or self.task:
            return 'หยุดบอทก่อนเปลี่ยนโหมด'
        text = open(CONFIG_PATH, encoding='utf-8').read()
        text, n = re.subn(r'(?m)^(mode\s*=\s*)[^#\n]*?(\s*(#.*)?)$',
                          lambda m: f'{m.group(1)}"{mode}"{m.group(2)}', text)
        if n != 1:
            return 'หา mode ใน config.toml ไม่เจอ'
        self._write_config(text)
        return None

    def save_build(self, values):
        """[build]: which class the Build tab shows, and the classes already at Lv.99
        (their Extra passives are unlocked)."""
        job = str(values.get('class', ''))
        done = [c for c in CLASSES if c in values.get('classes_99', [])]
        if job not in CLASSES:
            return 'ไม่รู้จักอาชีพนี้'
        lines = {'class': json.dumps(job), 'classes_99': json.dumps(done)}
        text = open(CONFIG_PATH, encoding='utf-8').read()
        if not re.search(r'(?m)^\[build\]', text):
            text = text.rstrip('\n') + ('\n\n# ----- แท็บ Build ของหน้าควบคุม -----\n[build]\n'
                                       'class = "Thief"\nclasses_99 = []   # อาชีพที่ถึง Lv.99 แล้ว (ปลด Extra ของอาชีพนั้น)\n')
        for key, value in lines.items():
            text, n = re.subn(rf'(?m)^({key}\s*=\s*)[^#\n]*?(\s*(#.*)?)$',
                              lambda m: f'{m.group(1)}{value}{m.group(2)}', text)
            if n != 1:
                return f'หา {key} ใน config.toml ไม่เจอ'
        self._write_config(text)
        return None

    def auto_build(self, body):
        """Build tab: the best Lv.99 stats, cards, Extras and set (buildsim). Always the full build; with ticked
        cards also one limited to them, the Extras of classes at Lv.99 and the easy sets."""
        job = str(body.get('job', ''))
        if job not in buildsim.CLASSES:
            return None, f'ยังคำนวณ {job or "อาชีพนี้"} ไม่ได้ ยังไม่ได้วัดสูตร (มี {", ".join(buildsim.CLASSES)})'

        def num(value, lo, hi):
            try:
                return min(hi, max(lo, float(value or 0)))
            except (TypeError, ValueError):
                return 0.0
        potion = str(body.get('potion') or '')
        args = dict(aspd_min=num(body.get('aspd_min'), 0, 190), crit_min=num(body.get('crit_min'), 0, 100),
                    farm=bool(body.get('farm')), potion=potion if potion in buildsim.POTIONS else buildsim.NO_POTION,
                    gear={k: num((body.get('gear') or {}).get(k), -999, 9999) for k in buildsim.GEAR_KEYS})
        owned = [str(n) for n in body.get('owned') or []]
        done = self.bot.cfg.get('build', {}).get('classes_99', [])
        try:
            full = buildsim.best_build(job, **args)
            now = buildsim.best_build(job, owned=set(owned), unlocked=done, easy_sets=True, **args) if owned else None
        except Exception as e:                    # a data file the simulator cannot read
            return None, f'คำนวณไม่สำเร็จ: {e}'
        return {'full': full, 'now': now, 'classes_99': done}, None

    def read_screen(self):
        """Build tab: read Status & Skills > Stats with the gear on (Game.read_stats_screen)."""
        log('อ่านหน้า Stats (ตอนใส่ของอยู่) ให้แท็บ Build')
        rd = self.bot.game.read_stats_screen()
        if not rd:
            log('อ่านหน้า Stats ไม่สำเร็จ')
            return
        self.screen_read = rd
        p = rd['panel']
        log(f"อ่านหน้า Stats แล้ว: ATK {p.get('ATK')} · ASPD {p.get('ASPD')} · Crit {p.get('Crit Rate')}%"
            + (f" · ลองกด + {', '.join(rd['previews'])}" if rd['previews'] else ' · ไม่มีแต้มว่างให้ลองกด +'))

    def gear_from_screen(self, body):
        """What the worn gear adds, from the last Stats screen reading. Uses the class read in game (the Bag
        title) over the one picked in the Build tab: on 2026-11-06 an Archer read as a Samurai gave ATK% -94."""
        job = str(body.get('job', ''))
        rd = getattr(self, 'screen_read', None)
        if not rd:
            return None, 'ยังไม่ได้อ่านหน้า Stats จากเกม กดปุ่ม "อ่านจากเกม" ก่อน'
        notes = []
        seen = rd.get('job')
        if seen and seen != job:
            notes.append(f'ตัวที่อ่านเป็น {seen} ไม่ใช่ {job or "อาชีพที่เลือก"} เลยคิดแบบ {seen}')
            job = seen
        if job not in buildsim.CLASSES:
            return None, f'ยังคำนวณ {job or "อาชีพนี้"} ไม่ได้'
        eq = body.get('equipped') or {}
        equipped = {'set': str(eq.get('set') or ''), 'cards': [str(c) for c in eq.get('cards') or [] if c],
                    'extras': [str(x) for x in eq.get('extras') or [] if x]}
        try:
            gear, more = buildsim.gear_from_reading(job, rd, equipped)
        except Exception as e:
            return None, f'คิดค่าชุดไม่สำเร็จ: {e}'
        return {'gear': gear, 'notes': notes + more, 'reading': rd, 'job': job}, None

    def save_boss(self, values):
        """Save the [boss] section's values, keeping the comments in the file."""
        targets = [str(t) for t in values.get('targets', [])]
        if any(not self.bot.find_monster(t) for t in targets):
            return 'มีชื่อบอสที่ไม่รู้จัก'
        new = {'targets': json.dumps(targets, ensure_ascii=False)}
        for key, lo, hi in (('appear_s', 2, 60), ('wait_respawn_s', 0, 600), ('after_kill_s', 0, 30), ('zoom_out_clicks', 0, 30), ('timed_wait_s', 0, 120),
                            ('server_cooldown_min', 1, 60), ('server_min_players', 0, 12)):
            try:
                num = int(values.get(key))
            except (TypeError, ValueError):
                return f'{key}: ใส่เป็นตัวเลข'
            if not lo <= num <= hi:
                return f'{key}: ต้องอยู่ระหว่าง {lo}–{hi}'
            new[key] = str(num)
        new['until_card'] = 'true' if values.get('until_card') else 'false'
        new['follow_timers'] = 'true' if values.get('follow_timers') else 'false'
        if values.get('server_pick') not in ('fewest', 'random', 'most'):
            return 'server_pick: เลือก fewest, random หรือ most'
        new['server_pick'] = json.dumps(values['server_pick'])
        try:
            skip = sorted(int(v) for v in values.get('server_skip', []))
        except (TypeError, ValueError):
            return 'จำนวนห้องที่ข้าม: ใส่เป็นตัวเลข'
        if len(skip) != 2 or not 0 <= skip[0] <= skip[1] <= 100:
            return 'จำนวนห้องที่ข้าม: ต้องอยู่ระหว่าง 0–100'
        new['server_skip'] = json.dumps(skip)
        text = open(CONFIG_PATH, encoding='utf-8').read()
        for key, value in new.items():
            text, n = re.subn(rf'(?m)^({key}\s*=\s*)[^#\n]*?(\s*(#.*)?)$',
                              lambda m: f'{m.group(1)}{value}{m.group(2)}', text)
            if n != 1:
                return f'หา {key} ใน config.toml ไม่เจอ'
        self._write_config(text)
        log('บันทึกการตั้งค่าฟาร์มบอสแล้ว: ' + (', '.join(targets) or 'ยังไม่ได้เลือกบอส'))
        return None

    def save_settings(self, values):
        text = open(CONFIG_PATH, encoding='utf-8').read()
        for key, raw in values.items():
            if key not in SETTINGS:
                continue
            kind, lo, hi = SETTINGS[key]
            if kind is bool:
                value = 'true' if raw else 'false'
            else:
                try:
                    num = kind(raw)
                except (TypeError, ValueError):
                    return f'{key}: ใส่เป็นตัวเลข'
                if not lo <= num <= hi:
                    return f'{key}: ต้องอยู่ระหว่าง {lo}–{hi}'
                if kind is float and 0 < num < 0.1:
                    return f'{key}: ต่ำสุด 0.1 (หรือ 0 = ไม่กด)'
                value = f'{num:g}'
            text, n = re.subn(rf'(?m)^({key}\s*=\s*)[^#\n]*?(\s*(#.*)?)$',
                              lambda m: f'{m.group(1)}{value}{m.group(2)}', text)
            if n != 1:
                return f'หา {key} ใน config.toml ไม่เจอ'
        self._write_config(text)
        log('บันทึกการตั้งค่าแล้ว')
        return None

    def _write_config(self, text):
        with open(CONFIG_PATH, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        self.bot.cfg = load_config()    # the running bot picks this up on its next round


def load_data(name):
    """A JSON file from data/, or {} when it is missing."""
    try:
        with open(os.path.join(DATA_DIR, name), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = False         # on Windows reuse lets a second copy bind the same port
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    panel = None

    def log_message(self, *args):       # keep the console for the bot's own log
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8', headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        size = int(self.headers.get('Content-Length') or 0)
        try:
            return json.loads(self.rfile.read(size) or b'{}')
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        url = urlparse(self.path)
        p = self.panel
        if url.path == '/api/state':
            after = int(parse_qs(url.query).get('after', ['0'])[0] or 0)
            return self._send(200, p.state(after))
        if url.path == '/api/config':
            return self._send(200, p.config())
        if url.path == '/api/stats/screen':
            rd = getattr(p, 'screen_read', None)
            return self._send(200, {'reading': rd, 'busy': p.task == 'screen'})
        if url.path == '/api/screen.jpg':
            data, shot_at = p.preview_jpeg()
            if not data:
                return self._send(503, {'error': 'no screen'})
            return self._send(200, data, 'image/jpeg', [('X-Shot-At', f'{shot_at:.2f}')])
        if url.path == '/api/trace':
            return self._send(200, p.trace(int(parse_qs(url.query).get('after', ['0'])[0] or 0)))
        name = 'index.html' if url.path in ('/', '') else url.path.lstrip('/')
        path = os.path.normpath(os.path.join(WEB_DIR, name))
        if not path.startswith(WEB_DIR) or not os.path.isfile(path):
            return self._send(404, {'error': 'not found'})
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        with open(path, 'rb') as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        p = self.panel
        body = self._body()
        path = urlparse(self.path).path
        busy = {'ok': False, 'error': 'บอทกำลังทำงานอยู่ หยุดบอทก่อน'}
        if path == '/api/start':
            return self._send(200, {'ok': True} if p.start() else busy)
        if path == '/api/boss/start':
            return self._send(200, {'ok': True} if p.start('boss') else busy)
        if path == '/api/mode':
            err = p.save_mode(body.get('mode'))
            return self._send(200, {'ok': not err, 'error': err})
        if path == '/api/boss':
            err = p.save_boss(body.get('boss', {}))
            return self._send(200, {'ok': not err, 'error': err})
        if path == '/api/stop':
            p.stop()
            return self._send(200, {'ok': True})
        if path == '/api/gear':
            return self._send(200, {'ok': True} if p.run_task('gear', p.gear) else busy)
        if path == '/api/hunt':
            monster = str(body.get('monster', ''))
            return self._send(200, {'ok': True} if p.run_task('hunt', lambda: p.hunt(monster)) else busy)
        if path == '/api/scan':
            return self._send(200, {'ok': True} if p.run_task('scan', p.scan) else busy)
        if path == '/api/cards/read':
            return self._send(200, {'ok': True} if p.run_task('cards', p.read_cards) else busy)
        if path == '/api/autobuild':
            res, err = p.auto_build(body)
            return self._send(200, {'ok': not err, 'error': err, **(res or {})})
        if path == '/api/stats/screen':
            return self._send(200, {'ok': True} if p.run_task('screen', p.read_screen) else busy)
        if path == '/api/autobuild/gear':
            res, err = p.gear_from_screen(body)
            return self._send(200, {'ok': not err, 'error': err, **(res or {})})
        if path == '/api/build':
            err = p.save_build(body.get('build', {}))
            return self._send(200, {'ok': not err, 'error': err})
        if path == '/api/stats':
            err = p.save_stats(body.get('job'), body.get('rows', []))
            return self._send(200, {'ok': not err, 'error': err})
        if path == '/api/stats/run':
            return self._send(200, {'ok': True} if p.run_task('stats', p.raise_stats) else busy)
        if path == '/api/plan':
            err = p.save_plan(body.get('plan', []))
            return self._send(200, {'ok': not err, 'error': err})
        if path == '/api/settings':
            err = p.save_settings(body.get('settings', {}))
            return self._send(200, {'ok': not err, 'error': err})
        self._send(404, {'error': 'not found'})


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if not os.path.exists(MONSTERS_PATH):
        print('ยังไม่มี data/monsters.json: เปิดหน้าควบคุมแล้วกด "สแกนข้อมูลมอนใหม่" ในแท็บมอนทั้งหมด')
    url = f'http://{HOST}:{PORT}'
    try:
        server = Server((HOST, PORT), Handler)
    except OSError:
        # already open in another window: a second copy would mean two bots on one game
        print(f'หน้าควบคุมเปิดอยู่แล้วที่ {url} จะเปิดเบราว์เซอร์ไปหน้านั้น')
        if '--no-browser' not in sys.argv:
            webbrowser.open(url)
        return
    Handler.panel = Panel()
    print(f'หน้าควบคุม BotLevel99: {url}  (ปิดหน้าต่างนี้หรือกด Ctrl+C เพื่อปิด)')
    if '--no-browser' not in sys.argv:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Handler.panel.stop()
        Handler.panel.bot.ocr.close()
        server.server_close()


if __name__ == '__main__':
    main()
