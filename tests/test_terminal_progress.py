import io
import unittest
from unittest.mock import patch

from terminal_progress import DynamicProgressBar


class ProgressBarTests(unittest.TestCase):
    def test_non_tty_reports_milestones_and_final_completion(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            bar = DynamicProgressBar(total=4, title="Test")
            bar.update(1, "first.pdf", "CONVERTING")
            bar.update(2, "second.pdf", "CONVERTING")
            bar.complete("Finished")

        lines = output.getvalue().splitlines()
        self.assertIn("25.0%", lines[0])
        self.assertIn("50.0%", lines[1])
        self.assertIn("100.0%", lines[-1])
        self.assertIn("Finished", lines[-1])

    def test_progress_never_moves_backwards(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            bar = DynamicProgressBar(total=10, title="Test")
            bar.update(7)
            bar.update(3)

        self.assertEqual(bar.current, 7)

    def test_activity_does_not_spam_non_tty_output(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            bar = DynamicProgressBar(total=2, title="Test")
            with bar.activity(0, "large.pdf", "CONVERTING"):
                pass

        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_tty_activity_redraws_the_live_line(self):
        class TTYOutput(io.StringIO):
            def isatty(self):
                return True

        output = TTYOutput()
        with patch("sys.stdout", output):
            bar = DynamicProgressBar(total=2, title="Test")
            with bar.activity(0, "large.pdf", "CONVERTING"):
                import time

                time.sleep(0.22)
                bar.update(0.5, "large.pdf", "PAGE 1/2")
            bar.complete("Finished")

        self.assertGreaterEqual(output.getvalue().count("\r"), 3)
        self.assertIn("CONVERTING", output.getvalue())
        self.assertIn("PAGE 1/2", output.getvalue())
        self.assertIn("100.0%", output.getvalue())


if __name__ == "__main__":
    unittest.main()
