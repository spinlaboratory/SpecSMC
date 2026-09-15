import queue
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from SpecSMC.gui import SMCGui


class GuiHarness:
    _run_task = SMCGui._run_task
    _poll_worker_results = SMCGui._poll_worker_results
    _motion_planner_worker = SMCGui._motion_planner_worker

    def __init__(self):
        self.owner = threading.get_ident()
        self.busy = False
        self._worker_results = queue.Queue()
        self._worker_poll_job = None
        self._set_busy = Mock()
        self._task_done = Mock(side_effect=self.check_owner)
        self._task_failed = Mock(side_effect=self.check_owner)
        self._motion_planner_finished = Mock(side_effect=self.check_owner)

    def check_owner(self, *args):
        assert threading.get_ident() == self.owner, 'Tk accessed from worker'

    def after(self, delay, callback):
        self.check_owner()
        return 'poll-job'

    def deliver(self):
        result = self._worker_results.get(timeout=3)
        self._worker_results.put(result)
        self._poll_worker_results()


class GuiThreadingTests(unittest.TestCase):
    def test_task_success_delivered_on_owner_thread(self):
        gui = GuiHarness()
        done = Mock()
        gui._run_task(lambda: threading.get_ident(), done, 'Failed')
        gui.deliver()
        callback, worker_id = gui._task_done.call_args.args
        self.assertIs(callback, done)
        self.assertNotEqual(worker_id, gui.owner)

    def test_task_failure_delivered_on_owner_thread(self):
        gui = GuiHarness()
        error = ValueError('invalid value')

        def task():
            raise error

        gui._run_task(task, Mock(), 'Failed')
        gui.deliver()
        gui._task_failed.assert_called_once_with('Failed', error)

    def test_motion_completion_and_failure_delivered_on_owner_thread(self):
        for error in (None, OSError('disconnected')):
            with self.subTest(error=error):
                gui = GuiHarness()
                gui.motion_planner_lock = threading.Lock()
                gui.motion_planner_queue = []
                gui.motion_planner_targets = {}
                gui.motion_planner_done_at = 0
                gui.smc = Mock()
                gui.smc.relative.side_effect = error
                gui.smc.position.return_value = {'Z': 1}
                with ThreadPoolExecutor(1) as pool:
                    pool.submit(gui._motion_planner_worker).result(timeout=3)
                gui._motion_planner_finished.assert_not_called()
                gui.deliver()
                gui._motion_planner_finished.assert_called_once_with(
                    error, None if error else {'Z': 1})

    def test_settings_read_tk_variables_before_worker_starts(self):
        cases = (
            ('_set_feedrate', 'feedrate_var', 'feedrates', 'feedrate'),
            ('_set_current', 'current_var', 'currents', 'current'),
            ('_set_homing_sensitivity', 'homing_sensitivity_var',
             'homing_sensitivities', 'homing_sensitivity'),
            ('_set_steps', 'steps_var', 'resolutions', 'steps_per_unit'),
        )
        for method, variable, cache, command in cases:
            with self.subTest(method=method):
                gui = GuiHarness()
                gui.smc = Mock()
                setattr(gui.smc, cache, {'Z': 1})
                gui._selected_settings_axis = lambda: 'Z'

                def read_value():
                    gui.check_owner()
                    return '2.5'

                setattr(gui, variable, SimpleNamespace(get=read_value))
                with ThreadPoolExecutor(1) as pool:
                    gui._call_smc = lambda task, *a, **kw: pool.submit(task).result(timeout=3)
                    getattr(SMCGui, method)(gui)
                getattr(gui.smc, command).assert_called_once_with('Z', 2.5)

    def test_callback_exception_keeps_polling_scheduled(self):
        gui = GuiHarness()
        gui._worker_results.put((Mock(side_effect=ValueError('callback')), ()))
        with self.assertRaises(ValueError):
            gui._poll_worker_results()
        self.assertEqual(gui._worker_poll_job, 'poll-job')

    def test_destroy_cancels_polling(self):
        gui = SMCGui.__new__(SMCGui)
        gui._worker_poll_job = 'poll-job'
        gui.after_cancel = Mock()
        with patch('tkinter.Tk.destroy') as destroy:
            SMCGui.destroy(gui)
        gui.after_cancel.assert_called_once_with('poll-job')
        self.assertIsNone(gui._worker_poll_job)
        destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
