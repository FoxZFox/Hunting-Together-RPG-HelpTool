"""Thin wrapper around adb / ldconsole for one LDPlayer instance.

Everything goes through adb inside the emulator, so the Windows mouse and
keyboard are never touched.
"""
import os
import re
import subprocess
import time

import cv2
import numpy as np

ROBLOX_PACKAGE = 'com.roblox.client'


class DeviceError(RuntimeError):
    pass


class Device:
    def __init__(self, ldplayer_dir, serial, index):
        self.adb_exe = os.path.join(ldplayer_dir, 'adb.exe')
        self.ldconsole_exe = os.path.join(ldplayer_dir, 'ldconsole.exe')
        self.serial = serial
        self.index = index

    def _run(self, args, timeout):
        try:
            return subprocess.run(args, capture_output=True, timeout=timeout,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as e:
            raise DeviceError(f'timeout: {" ".join(args[1:])}') from e

    def adb(self, *args, timeout=20):
        return self._run([self.adb_exe, '-s', self.serial, *args], timeout)

    def shell(self, cmd, timeout=20):
        return self.adb('shell', cmd, timeout=timeout).stdout.decode('utf-8', 'replace')

    def ldconsole(self, command, *args, timeout=60):
        out = self._run([self.ldconsole_exe, command, '--index', str(self.index), *args], timeout)
        return out.stdout.decode('utf-8', 'replace').strip()

    def screenshot(self):
        data = self.adb('exec-out', 'screencap', '-p').stdout
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
        if img is None:
            raise DeviceError('screencap failed (is LDPlayer running?)')
        return img

    def tap(self, x, y):
        self.shell(f'input tap {int(x)} {int(y)}')

    def swipe(self, x1, y1, x2, y2, ms=500):
        self.shell(f'input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}')

    def keyboard_device(self):
        """The emulator's own keyboard, e.g. /dev/input/event2. Roblox ignores `input keyevent`
        but takes key events written to this device, like a real key press in LDPlayer."""
        if not getattr(self, '_keyboard', None):
            self._keyboard = '/dev/input/event2'
            for block in self.shell('getevent -p').split('add device')[1:]:
                path = re.search(r'(/dev/input/event\d+)', block)
                name = re.search(r'name:\s*"([^"]*)"', block)
                if path and name and name.group(1) not in ('gpio', 'mouse') and 'KEY (0001)' in block \
                        and ' 0030 ' in block:
                    self._keyboard = path.group(1)
                    break
        return self._keyboard

    def mouse_device(self):
        """LDPlayer's own virtual mouse inside Android (e.g. /dev/input/event4)."""
        if not getattr(self, '_mouse', None):
            self._mouse = '/dev/input/event4'
            for block in self.shell('getevent -p').split('add device')[1:]:
                path = re.search(r'(/dev/input/event\d+)', block)
                name = re.search(r'name:\s*"([^"]*)"', block)
                if path and name and name.group(1) == 'mouse':
                    self._mouse = path.group(1)
                    break
        return self._mouse

    def wheel(self, clicks):
        """Turn the wheel of the emulator's virtual mouse (the Windows mouse is not touched).
        Negative = towards you, which zooms a Roblox camera out."""
        dev = self.mouse_device()
        for _ in range(abs(clicks)):
            self.shell(f'sendevent {dev} 2 8 {-1 if clicks < 0 else 1}; sendevent {dev} 0 0 0')
            time.sleep(0.2)

    def key(self, code):
        """Press and release one key (Linux key code, e.g. 48 = B)."""
        dev = self.keyboard_device()
        self.shell(f'sendevent {dev} 1 {code} 1; sendevent {dev} 0 0 0; '
                   f'sendevent {dev} 1 {code} 0; sendevent {dev} 0 0 0')

    def focused_package(self):
        out = self.shell('dumpsys window | grep mCurrentFocus')
        m = re.search(r'\s([\w.]+)/[\w.$]+\}', out)
        return m.group(1) if m else ''

    def roblox_in_front(self):
        return self.focused_package() == ROBLOX_PACKAGE

    def open_url(self, url):
        self.shell(f'am start -a android.intent.action.VIEW -d "{url}" {ROBLOX_PACKAGE}')

    def stop_roblox(self):
        self.shell(f'am force-stop {ROBLOX_PACKAGE}')

    def emulator_running(self):
        return self.ldconsole('isrunning') == 'running'

    def responsive(self):
        """Does Android inside LDPlayer still answer? (LDPlayer can hang with its window still open.)"""
        try:
            return self.shell('echo ok', timeout=10).strip() == 'ok'
        except DeviceError:
            return False

    def start_emulator(self, timeout=180):
        """Launch this LDPlayer instance; True once Android has finished booting."""
        self.ldconsole('launch')
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(5)
            try:
                if self.shell('getprop sys.boot_completed', timeout=10).strip() == '1':
                    time.sleep(10)
                    return True
            except DeviceError:
                pass
        return False

    def restart_emulator(self, timeout=240):
        """Close this LDPlayer instance, reset adb, and open it again."""
        self.ldconsole('quit')
        end = time.time() + 60
        while time.time() < end and self.emulator_running():
            time.sleep(3)
        # a stuck adb server can outlive the emulator, so start it fresh too
        self._run([self.adb_exe, 'kill-server'], 20)
        time.sleep(3)
        self._keyboard = self._mouse = None
        return self.start_emulator(timeout)
