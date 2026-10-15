#!/usr/bin/python3
"""Local UU session -> existing BetterDisplay virtual screen. No network access."""
import contextlib
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import queue
import threading

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state'
BD = '/Applications/BetterDisplay.app/Contents/MacOS/BetterDisplay'
PROBE = ROOT / 'UU Display Switch.app/Contents/MacOS/session-probe'
PHYSICAL = []
PHYSICAL_KEYS = []
VIRTUAL = 'tagID=1'
STOP = False

class DisplayError(RuntimeError):
    pass

class BetterDisplay:
    def command(self, operation, target, feature):
        result = subprocess.run([BD, *operation.split(), *(['-' + target] if target else []), '-' + feature],
                                capture_output=True, text=True, timeout=5)
        output = result.stdout.strip()
        if result.returncode or output.lower().startswith('failed'):
            raise DisplayError('BetterDisplay rejected %s %s %s' % (operation, target, feature))
        return output

    def perform(self, feature):
        self.command('perform', None, feature)

    def get(self, target, feature='connected'):
        return self.parse(self.command('get', target, feature), feature)

    @staticmethod
    def parse(value, feature):
        value = value.lower()
        # A connected virtual screen reports both its definition and backing display (on,on).
        values = [item.strip() for item in value.split(',')]
        if not values or any(item not in ('on', 'off', 'true', 'false') for item in values):
            raise DisplayError('Unexpected %s response: %r' % (feature, value))
        flags = {item in ('on', 'true') for item in values}
        if len(flags) != 1:
            raise DisplayError('Inconsistent %s response: %r' % (feature, value))
        return flags.pop()

    def set(self, target, feature, value):
        # Reconfiguration can briefly report a missing display or mixed virtual/backing states.
        try:
            result = self.command('set get', target, feature + '=' + ('on' if value else 'off'))
            if self.parse(result, feature) == value:
                return
        except DisplayError:
            pass
        for _ in range(3):
            time.sleep(0.5)
            try:
                if self.get(target, feature) == value:
                    return
            except DisplayError:
                pass
        raise DisplayError('Display state did not settle: ' + target + ' ' + feature)

class Controller:
    def __init__(self, bd, directory):
        self.bd = bd
        self.directory = directory
        self.marker = directory / 'restore.json'
        self.pause = directory / 'paused'
        directory.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def lock(self):
        with (self.directory / 'switch.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def restore(self):
        if not self.marker.exists():
            return
        started = time.monotonic()
        saved = json.loads(self.marker.read_text())
        try:
            self.bd.perform('connectAllDisplays')
        except Exception:
            logging.warning('Bulk reconnect failed; checking each physical screen')
        errors = []
        # Only retry individually when the bulk operation did not reconnect a screen.
        for uuid in PHYSICAL:
            try:
                try:
                    connected = self.bd.get('UUID=' + uuid)
                except Exception:
                    connected = False
                if not connected:
                    self.bd.set('UUID=' + uuid, 'connected', True)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise DisplayError('; '.join(errors))
        main = saved.get('main') or PHYSICAL[0]
        self.bd.set('UUID=' + main, 'main', True)
        # Keep the virtual screen as fallback unless all physical screens are verified.
        self.bd.set(VIRTUAL, 'connected', False)
        self.marker.unlink()
        logging.info('LOCAL: configured physical screens restored; virtual screen disconnected (%.1fs)', time.monotonic() - started)

    def enter(self):
        original_main = next((u for u in PHYSICAL if self.bd.get('UUID=' + u, 'main')), PHYSICAL[0])
        temporary = self.marker.with_suffix('.tmp')
        with temporary.open('w') as handle:
            json.dump({'main': original_main}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.marker)
        try:
            self.bd.set(VIRTUAL, 'connected', True)
            self.bd.set(VIRTUAL, 'main', True)
            if STOP or self.pause.exists():
                raise DisplayError('Switch cancelled')
            if not self.bd.get(VIRTUAL) or not self.bd.get(VIRTUAL, 'main'):
                raise DisplayError('Virtual screen is not ready as the main display')
            self.bd.perform('disconnectAllButMain')
            for attempt in range(3):
                try:
                    if self.healthy():
                        break
                except DisplayError:
                    pass
                if attempt == 2:
                    raise DisplayError('Final screen verification failed')
                time.sleep(0.5)
            logging.info('REMOTE: existing virtual screen active; configured physical screens disconnected')
        except Exception:
            self.pause.touch()
            self.restore()
            raise

    def healthy(self):
        return self.bd.get(VIRTUAL) and not any(self.bd.get('UUID=' + u) for u in PHYSICAL)


def probe():
    try:
        result = subprocess.run([str(PROBE)], capture_output=True, text=True, timeout=4)
        state = result.stdout.strip()
        if result.returncode == 0 and state in ('connected', 'disconnected', 'unknown', 'permission-required'):
            return state
    except (OSError, subprocess.TimeoutExpired):
        pass
    return 'unknown'


def stop(signum, frame):
    global STOP
    STOP = True


class LockPolicy:
    def __init__(self):
        self.locked = False
        self.lock_at = None
        self.wanted = False
        self.remote_seen = False
        self.last_remote = None
        self.local_return = False
        self.rearm_after = None
        self.awake_seen = False
        self.sleep_since = None

    def event(self, row, now):
        kind = row.get('kind')
        if row.get('name') == 'com.apple.screenIsLocked':
            self.local_return = False
            self.rearm_after = None
            self.locked = True
            self.lock_at = now
            self.wanted = True
            self.remote_seen = False
            self.last_remote = None
        elif row.get('name') == 'com.apple.screenIsUnlocked':
            self.locked = False
            self.local_return = False
        elif kind == 'local' and self.lock_at is not None and now - self.lock_at >= 2:
            if self.wanted:
                self.local_return = self.locked
                self.rearm_after = None
            self.sleep_since = None
            self.wanted = False
        elif kind == 'remote' and self.wanted:
            self.remote_seen = True
            self.last_remote = now

    def physical_restored(self, now):
        if self.local_return:
            self.rearm_after = now + 3
            self.awake_seen = False
            self.sleep_since = None

    def physical_sleep(self, snapshot, now, session):
        if (not self.locked or not self.local_return or self.wanted or
                self.rearm_after is None or now < self.rearm_after):
            return
        if snapshot.get('allAwake') is True:
            self.awake_seen = True
        if session == 'connected' or snapshot.get('allAsleep') is not True or not self.awake_seen:
            self.sleep_since = None
            return
        if self.sleep_since is None:
            self.sleep_since = now
        elif now - self.sleep_since >= 1:
            self.wanted = True
            self.local_return = False
            self.remote_seen = False
            self.last_remote = None

    def session(self, state, now):
        if state == 'connected' and self.wanted:
            self.remote_seen = True
            self.last_remote = now
        if self.wanted and self.remote_seen:
            grace = 5 if state == 'disconnected' else (120 if self.locked else 12)
            if state != 'connected' and now - self.last_remote >= grace:
                self.wanted = False


def physical_sleep_snapshot():
    result = subprocess.run([str(ROOT / 'display-sleep'), *PHYSICAL_KEYS], capture_output=True, text=True, timeout=2, check=True)
    row = json.loads(result.stdout)
    if not isinstance(row, dict) or any(type(row.get(key)) is not bool for key in ('allAwake', 'allAsleep')):
        raise DisplayError('Invalid physical sleep state')
    return row


def wake_for_rearm():
    # BetterDisplay cannot reliably change the main display while all displays sleep.
    subprocess.run(['/usr/bin/caffeinate', '-u', '-t', '2'], timeout=4, check=True)
    for _ in range(6):
        if physical_sleep_snapshot()['allAwake']:
            return
        time.sleep(.5)
    raise DisplayError('Physical displays did not wake before rearm')


def run(controller):
    if controller.marker.exists():
        controller.pause.touch()
    # Incoming-session detection needs UU's window, not only its boot daemon.
    subprocess.run(['/usr/bin/open', '-g', '-a', '/Applications/UURemote.app'],
                   timeout=10, check=True)
    policy = LockPolicy()
    events = queue.Queue()
    listener = subprocess.Popen([str(ROOT / 'input-events')], stdout=subprocess.PIPE,
                                text=True, bufsize=1)
    def read_events():
        try:
            for line in listener.stdout:
                events.put((json.loads(line), time.monotonic()))
        except Exception:
            logging.exception('Input listener stream failed')
    threading.Thread(target=read_events, daemon=True).start()
    heartbeat = time.monotonic()
    ready = False
    last_check = last_probe = last_sleep_probe = 0.0
    state = 'unknown'
    last_state = None
    try:
        while not STOP:
            now = time.monotonic()
            while True:
                try:
                    row, stamp = events.get_nowait()
                except queue.Empty:
                    break
                if row.get('kind') in ('ready', 'heartbeat'):
                    heartbeat = stamp
                    ready = row.get('inputTap') is True
                    if not ready:
                        controller.pause.touch()
                else:
                    previous = policy.wanted
                    policy.event(row, stamp)
                    if previous != policy.wanted:
                        logging.info('Display request: virtual=%s event=%s', policy.wanted,
                                     row.get('name') or row.get('kind'))
            failed = listener.poll() is not None or now - heartbeat > 5 or (not ready and now - heartbeat > 2)
            if failed:
                controller.pause.touch()
                policy.wanted = False
            if now - last_probe >= 1:
                state = probe()
                last_probe = time.monotonic()
                policy.session(state, last_probe)
                if state != last_state:
                    logging.info('UU state: %s', state)
                    last_state = state
            status = {'uu': state, 'checked_at': time.time(), 'locked': policy.locked,
                      'virtual_requested': policy.wanted, 'input_listener': ready and not failed}
            status_tmp = controller.directory / 'status.tmp'
            status_tmp.write_text(json.dumps(status))
            status_tmp.replace(controller.directory / 'status.json')
            with controller.lock():
                try:
                    if (not controller.pause.exists() and policy.local_return and
                            not controller.marker.exists() and now - last_sleep_probe >= 1):
                        policy.physical_sleep(physical_sleep_snapshot(), time.monotonic(), state)
                        last_sleep_probe = time.monotonic()
                        if policy.wanted:
                            logging.info('Display request: virtual=True event=physical_sleep_after_local_return')
                            wake_for_rearm()
                    if controller.pause.exists() or not policy.wanted:
                        restoring = controller.marker.exists()
                        controller.restore()
                        if restoring:
                            policy.physical_restored(time.monotonic())
                    elif ready and not controller.marker.exists():
                        controller.enter()
                        last_check = time.monotonic()
                    elif controller.marker.exists() and now - last_check >= 8:
                        if not controller.healthy():
                            raise DisplayError('Unexpected screen change')
                        last_check = time.monotonic()
                except Exception:
                    logging.exception('Switch failed; paused, recovery will retry')
                    controller.pause.touch()
                    policy.wanted = False
            if failed:
                # Restart the listener only after physical recovery; keep automation paused.
                if not controller.marker.exists():
                    raise DisplayError('Input listener unavailable; restored and paused')
            time.sleep(.2)
    finally:
        listener.terminate()
        try:
            listener.wait(timeout=3)
        except subprocess.TimeoutExpired:
            listener.kill()
            listener.wait()
        with controller.lock():
            controller.restore()


def load_config(path):
    import uuid
    global VIRTUAL
    data = json.loads(path.read_text())
    screens = data['physical_displays']
    if not isinstance(screens, list) or not screens:
        raise ValueError('physical_displays must be a non-empty list')
    identifiers, keys = [], []
    for screen in screens:
        identifiers.append(str(uuid.UUID(screen['uuid'])).upper())
        key = screen['hardware_key']
        if not isinstance(key, str) or len(key.split(':')) != 3 or not all(x.isdecimal() for x in key.split(':')):
            raise ValueError('hardware_key must be vendor:model:serial in decimal')
        keys.append(key)
    if len(set(identifiers)) != len(screens) or len(set(keys)) != len(screens):
        raise ValueError('Duplicate display identity; indistinguishable monitors are unsupported')
    tag = data['virtual_tag_id']
    if type(tag) is not int or tag <= 0:
        raise ValueError('virtual_tag_id must be a positive integer')
    PHYSICAL[:] = identifiers
    PHYSICAL_KEYS[:] = keys
    VIRTUAL = 'tagID=' + str(tag)


def main():
    load_config(ROOT / 'config.json')
    STATE.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(STATE / 'switcher.log', maxBytes=500000, backupCount=2)
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    controller = Controller(BetterDisplay(), STATE)
    action = sys.argv[1] if len(sys.argv) > 1 else '--daemon'
    if action == '--resume':
        with controller.lock():
            controller.restore()
            controller.pause.unlink(missing_ok=True)
        print('自动切换已启用。')
    elif action == '--restore':
        controller.pause.touch()
        with controller.lock():
            # Also provides recovery when no active session marker exists.
            if not controller.marker.exists():
                controller.marker.write_text(json.dumps({'main': PHYSICAL[0]}))
            controller.restore()
        print('已暂停自动切换，恢复配置的物理屏，关闭虚拟屏。')
    elif action == '--status':
        status_file = STATE / 'status.json'
        status = json.loads(status_file.read_text()) if status_file.exists() else {'uu': 'not-started'}
        status.update({'paused': controller.pause.exists(),
                       'remote_or_recovery_pending': controller.marker.exists()})
        print(json.dumps(status, ensure_ascii=False))
    elif action == '--daemon':
        # Keep a single controller even if accidentally launched twice.
        with (STATE / 'daemon.lock').open('a') as daemon_lock:
            try:
                fcntl.flock(daemon_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                run(controller)
            finally:
                with controller.lock():
                    controller.restore()
    else:
        raise SystemExit('Unknown action')

if __name__ == '__main__':
    main()
