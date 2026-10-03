import io
import importlib.util
import unittest
from unittest.mock import patch

from setup_progress import PlainBackend, RichBackend, STATE_DONE, STATE_RUNNING


class TTYOutput(io.StringIO):
    def isatty(self):
        return True


class SetupProgressTests(unittest.TestCase):
    def test_plain_tty_animates_active_tasks_and_clears_on_finish(self):
        output = TTYOutput()
        with patch("sys.stdout", output):
            backend = PlainBackend()
            backend.task_add("deps", "Python dependencies")
            backend.task_start("deps", "pip install")
            backend.tick()
            backend.task_log("deps", "Installing package")
            backend.task_done("deps")
            backend.close()

        rendered = output.getvalue()
        self.assertIn("[>>>] Python dependencies [", rendered)
        self.assertIn("Installing package", rendered)
        self.assertIn("[ OK] Python dependencies", rendered)
        self.assertEqual(backend._state["deps"], STATE_DONE)

    def test_plain_non_tty_updates_details_without_log_spam(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            backend = PlainBackend()
            backend.task_add("venv", "Virtual environment")
            backend.task_start("venv", "creating")
            backend.task_log("venv", "creating interpreter")

        self.assertEqual(backend._state["venv"], STATE_RUNNING)
        self.assertEqual(output.getvalue().count("\n"), 2)
        self.assertEqual(backend._details["venv"], "creating interpreter")

    @unittest.skipUnless(importlib.util.find_spec("rich"), "Rich is optional")
    def test_rich_backend_uses_indeterminate_bar_while_running(self):
        backend = RichBackend()
        try:
            backend.task_add("deps", "Python dependencies")
            backend.task_start("deps", "pip install")
            task = backend.progress.tasks[backend._ids["deps"]]
            self.assertIsNone(task.total)

            backend.task_done("deps")
            task = backend.progress.tasks[backend._ids["deps"]]
            self.assertEqual(task.total, 1)
            self.assertEqual(task.completed, 1)
        finally:
            backend.close()


if __name__ == "__main__":
    unittest.main()
