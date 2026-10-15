import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path
from switcher import Controller, PHYSICAL, VIRTUAL, DisplayError, BetterDisplay

class FakeDisplay:
    def __init__(self):
        self.states = {'UUID=' + u: True for u in PHYSICAL}
        self.states[VIRTUAL] = False
        self.main = 'UUID=' + PHYSICAL[1]
        self.fail = None
        self.calls = []
        self.bulk_fail = None
    def perform(self, feature):
        self.calls.append(('bulk', feature, True))
        if feature == self.bulk_fail:
            raise DisplayError('bulk failure')
        for uuid in PHYSICAL:
            self.set('UUID=' + uuid, 'connected', feature == 'connectAllDisplays')

    def get(self, target, feature='connected'):

        return target == self.main if feature == 'main' else self.states[target]
    def set(self, target, feature, value):
        self.calls.append((target, feature, value))
        if self.fail == (target, feature, value):
            raise DisplayError('injected failure')
        if feature == 'main':
            self.main = target
        else:
            self.states[target] = value

class Tests(unittest.TestCase):
    def setUp(self):
        PHYSICAL[:] = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333"]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bd = FakeDisplay()
        self.c = Controller(self.bd, Path(self.tmp.name))
    def test_actual_virtual_screen_response(self):
        bd = BetterDisplay()
        bd.command = lambda *args: 'on,on'
        self.assertTrue(bd.get(VIRTUAL))
        bd.command = lambda *args: 'off'
        self.assertFalse(bd.get(VIRTUAL))
        bd.command = lambda *args: 'on,off'
        with self.assertRaises(DisplayError): bd.get(VIRTUAL)
    def test_set_get_checks_returned_state(self):
        bd = BetterDisplay()
        calls = []
        def command(*args):
            calls.append(args)
            return 'on,on'
        bd.command = command
        bd.set(VIRTUAL, 'connected', True)
        self.assertEqual(calls, [('set get', VIRTUAL, 'connected=on')])
        bd.command = lambda *args: 'on,off'
        with patch('switcher.time.sleep'):
            with self.assertRaises(DisplayError): bd.set(VIRTUAL, 'connected', True)
    def test_transient_state_and_query_failure_recover(self):
        bd = BetterDisplay()
        results = iter(['on,off', DisplayError('temporarily missing'), 'off'])
        def command(*args):
            result = next(results)
            if isinstance(result, Exception): raise result
            return result
        bd.command = command
        with patch('switcher.time.sleep'):
            bd.set(VIRTUAL, 'connected', False)
    def test_enter_and_restore_order_and_original_main(self):
        self.c.enter()
        self.assertTrue(self.c.healthy())
        self.assertTrue(self.c.marker.exists())
        self.c.restore()
        self.assertTrue(all(self.bd.states['UUID=' + u] for u in PHYSICAL))
        self.assertFalse(self.bd.states[VIRTUAL])
        self.assertEqual(self.bd.main, 'UUID=' + PHYSICAL[1])
        self.assertEqual(self.bd.calls[-1], (VIRTUAL, 'connected', False))
        self.assertFalse(self.c.marker.exists())
    def test_virtual_failure_never_disconnects_physical(self):
        self.bd.fail = (VIRTUAL, 'connected', True)
        with self.assertRaises(DisplayError): self.c.enter()
        self.assertTrue(self.c.pause.exists())
        self.assertTrue(all(self.bd.states['UUID=' + u] for u in PHYSICAL))
        self.assertFalse(any(t.startswith('UUID=') and f == 'connected' and not v for t,f,v in self.bd.calls))
    def test_partial_disconnect_rolls_back(self):
        self.bd.fail = ('UUID=' + PHYSICAL[1], 'connected', False)
        with self.assertRaises(DisplayError): self.c.enter()
        self.assertTrue(all(self.bd.states['UUID=' + u] for u in PHYSICAL))
        self.assertFalse(self.bd.states[VIRTUAL])
    def test_restore_failure_keeps_virtual_and_retries(self):
        self.c.enter()
        self.bd.fail = ('UUID=' + PHYSICAL[0], 'connected', True)
        with self.assertRaises(DisplayError): self.c.restore()
        self.assertTrue(self.bd.states[VIRTUAL])
        self.assertTrue(self.c.marker.exists())
        self.assertTrue(self.bd.states['UUID=' + PHYSICAL[2]])
        self.bd.fail = None
        self.c.restore()
        self.assertFalse(self.c.marker.exists())
    def test_bulk_restore_failure_uses_individual_fallback(self):
        self.c.enter()
        self.bd.bulk_fail = 'connectAllDisplays'
        self.c.restore()
        self.assertTrue(all(self.bd.states['UUID=' + u] for u in PHYSICAL))
        self.assertFalse(self.bd.states[VIRTUAL])
    def test_bulk_disconnect_failure_rolls_back(self):
        self.bd.bulk_fail = 'disconnectAllButMain'
        with self.assertRaises(DisplayError): self.c.enter()
        self.assertTrue(self.c.pause.exists())
        self.assertTrue(all(self.bd.states['UUID=' + u] for u in PHYSICAL))
    def test_restart_can_restore_from_disk(self):
        self.c.enter()
        Controller(self.bd, Path(self.tmp.name)).restore()
        self.assertFalse(self.bd.states[VIRTUAL])
        self.assertTrue(all(self.bd.states['UUID=' + u] for u in PHYSICAL))


class LockPolicyTests(unittest.TestCase):
    def policy(self):
        from switcher import LockPolicy
        p = LockPolicy()
        p.event({'name':'com.apple.screenIsLocked'}, 0)
        return p
    def test_waiting_locked_does_not_look_like_dropout(self):
        p = self.policy()
        p.session('unknown', 10000)
        self.assertTrue(p.wanted)
    def test_remote_input_and_unlock_do_not_restore(self):
        p = self.policy()
        p.event({'kind':'remote'}, 10)
        p.event({'name':'com.apple.screenIsUnlocked'}, 11)
        p.session('connected', 12)
        self.assertTrue(p.wanted)
    def test_local_wins_even_with_connected_remote(self):
        p = self.policy()
        p.event({'kind':'local'}, 10)
        p.session('connected', 11)
        p.event({'kind':'remote'}, 12)
        self.assertFalse(p.wanted)
        p.event({'name':'com.apple.screenIsLocked'}, 20)
        self.assertTrue(p.wanted)
    def test_lock_chord_release_ignored(self):
        p = self.policy()
        p.event({'kind':'local'}, 1)
        self.assertTrue(p.wanted)
    def test_dropout_and_locked_unknown_grace(self):
        p = self.policy()
        p.session('connected', 10)
        p.session('disconnected', 14)
        self.assertTrue(p.wanted)
        p.session('disconnected', 15)
        self.assertFalse(p.wanted)
        p = self.policy()
        p.event({'kind':'remote'}, 10)
        p.session('unknown', 129)
        self.assertTrue(p.wanted)
        p.session('unknown', 130)
        self.assertFalse(p.wanted)
    def test_unlocked_unknown_grace(self):
        p = self.policy()
        p.event({'kind':'remote'}, 10)
        p.event({'name':'com.apple.screenIsUnlocked'}, 11)
        p.session('unknown', 21)
        self.assertTrue(p.wanted)
        p.session('unknown', 22)
        self.assertFalse(p.wanted)

class RelockTests(unittest.TestCase):
    def policy(self):
        from switcher import LockPolicy
        p = LockPolicy()
        p.event({'name':'com.apple.screenIsLocked'}, 0)
        p.event({'kind':'local'}, 10)
        p.physical_restored(20)
        return p
    def test_real_sleep_rearms_once_and_ignores_switch_notifications(self):
        p = self.policy()
        p.physical_sleep({'allAsleep':True}, 22, 'unknown')
        p.physical_sleep({'allAsleep':True}, 25, 'unknown')
        self.assertFalse(p.wanted)
        p.physical_sleep({'allAwake':True}, 26, 'unknown')
        p.physical_sleep({'allAsleep':True}, 30, 'unknown')
        self.assertFalse(p.wanted)
        p.physical_sleep({'allAsleep':True}, 31, 'unknown')
        self.assertTrue(p.wanted)
        self.assertFalse(p.local_return)
        p.event({'name':'NSWorkspaceScreensDidWakeNotification'}, 32)
        p.session('disconnected', 100)
        self.assertTrue(p.wanted)
    def test_unlocked_or_remote_session_does_not_rearm(self):
        for mode in ['unlocked', 'connected']:
            p = self.policy()
            p.physical_sleep({'allAwake':True}, 25, 'unknown')
            if mode == 'unlocked': p.event({'name':'com.apple.screenIsUnlocked'}, 26)
            for t in (30, 32): p.physical_sleep({'allAsleep':True}, t, mode)
            self.assertFalse(p.wanted)
    def test_missing_screen_and_local_input_reset_sleep_confirmation(self):
        p = self.policy()
        p.physical_sleep({'allAwake':True}, 25, 'unknown')
        p.physical_sleep({'allAsleep':True}, 30, 'unknown')
        p.physical_sleep({'allAwake':False,'allAsleep':False}, 31, 'unknown')
        p.physical_sleep({'allAsleep':True}, 32, 'unknown')
        self.assertFalse(p.wanted)
        p.event({'kind':'local'}, 32.5)
        p.physical_sleep({'allAsleep':True}, 33, 'unknown')
        self.assertFalse(p.wanted)
    def test_dropout_restore_does_not_rearm(self):
        from switcher import LockPolicy
        p = LockPolicy()
        p.event({'name':'com.apple.screenIsLocked'}, 0)
        p.session('connected', 1)
        p.session('disconnected', 7)
        p.physical_restored(20)
        p.physical_sleep({'allAwake':True}, 25, 'unknown')
        for t in (30,32): p.physical_sleep({'allAsleep':True}, t, 'unknown')
        self.assertFalse(p.wanted)

class WakeTests(unittest.TestCase):
    def test_wake_before_rearm_and_fail_closed(self):
        from switcher import wake_for_rearm
        with patch('switcher.subprocess.run') as cmd, patch('switcher.physical_sleep_snapshot', return_value={'allAwake':True}):
            wake_for_rearm()
            self.assertEqual(cmd.call_args[0][0], ['/usr/bin/caffeinate','-u','-t','2'])
        with patch('switcher.subprocess.run'), patch('switcher.physical_sleep_snapshot', return_value={'allAwake':False}), patch('switcher.time.sleep'):
            with self.assertRaises(DisplayError): wake_for_rearm()

class ConfigTests(unittest.TestCase):
    def test_config_validation(self):
        import json, switcher
        saved = (switcher.PHYSICAL[:], switcher.PHYSICAL_KEYS[:], switcher.VIRTUAL)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'config.json'
                good = {'physical_displays':[{'uuid':'11111111-1111-1111-1111-111111111111','hardware_key':'1234:5678:0'}], 'virtual_tag_id':7}
                path.write_text(json.dumps(good))
                switcher.load_config(path)
                self.assertEqual(switcher.VIRTUAL, 'tagID=7')
                self.assertEqual(switcher.PHYSICAL_KEYS, ['1234:5678:0'])
                for bad in [dict(good, physical_displays=[]), dict(good, virtual_tag_id=True), dict(good, physical_displays=good['physical_displays']*2), dict(good, physical_displays=[{'uuid':'placeholder','hardware_key':'1234:5678:0'}])]:
                    path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError): switcher.load_config(path)
        finally:
            switcher.PHYSICAL[:], switcher.PHYSICAL_KEYS[:], switcher.VIRTUAL = saved

if __name__ == '__main__': unittest.main()
