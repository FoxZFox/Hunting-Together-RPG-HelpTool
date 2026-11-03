"""Windows built-in OCR (via ocr.ps1 running under Windows PowerShell 5.1)."""
import json
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass

import cv2
import numpy as np

PS1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ocr.ps1')


@dataclass
class Line:
    text: str
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2


class Ocr:
    def __init__(self):
        self.proc = None
        self.tmp_dir = tempfile.mkdtemp(prefix='botlevel99_ocr_')
        self.count = 0
        self.lock = threading.Lock()      # one request at a time: the panel and the bot share it

    def _ensure(self):
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', PS1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)

    def ask_image(self, img):
        """OCR a whole image as-is. Each request gets its own file: WinRT can serve
        a stale copy when the same path is rewritten quickly."""
        with self.lock:
            self.count += 1
            path = os.path.join(self.tmp_dir, f'{self.count}.png')
            cv2.imwrite(path, img)
            try:
                return self._ask(path)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _ask(self, path, timeout=20):
        self._ensure()
        result = {}

        def work():
            self.proc.stdin.write(path + '\n')
            self.proc.stdin.flush()
            result['line'] = self.proc.stdout.readline()

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(timeout)
        if 'line' not in result or not result['line']:
            self.close()
            return {'lines': []}
        return json.loads(result['line'])

    def read(self, img, rect=None, scale=2.0, mode='gray'):
        """OCR a region of a BGR screenshot. Returned boxes are in screen pixels.

        mode: 'gray' for UI panels, 'white' to keep only near-white pixels (outlined HUD
        text drawn over the game world), 'color' to send the pixels unchanged.
        """
        x0, y0 = 0, 0
        if rect:
            x0, y0, w, h = rect
            img = img[y0:y0 + h, x0:x0 + w]
        if mode == 'white':
            img = 255 - cv2.inRange(img, (200, 200, 200), (255, 255, 255))
        elif mode == 'gray':
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        elif mode == 'dark':
            img = np.where(img.max(axis=2) < 110, 0, 255).astype(np.uint8)
        pad = 16
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        out = self.ask_image(img)
        return [Line(l['text'],
                     int((l['x'] - pad) / scale) + x0, int((l['y'] - pad) / scale) + y0,
                     int(l['w'] / scale), int(l['h'] / scale))
                for l in out.get('lines', [])]

    def text(self, img, rect=None, scale=2.0, mode='gray'):
        return ' '.join(l.text for l in self.read(img, rect, scale, mode))

    def close(self):
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc = None
