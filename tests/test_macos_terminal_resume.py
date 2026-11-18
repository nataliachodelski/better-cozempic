"""macOS auto-resume reopens Claude in the user's terminal: iTerm or Terminal.

`COZEMPIC_TERMINAL` wins; otherwise `TERM_PROGRAM=iTerm.app` picks iTerm; anything
else keeps the Terminal default. The generated osascript command must survive bash
parsing with quotes and spaces in the project path intact.
"""

import os
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from cozempic.helpers import macos_resume_command, macos_terminal_app, shell_quote


def _env(**values):
    """Patch the environment with only the terminal-selection vars given."""
    cleared = {k: v for k, v in os.environ.items() if k not in ("COZEMPIC_TERMINAL", "TERM_PROGRAM")}
    cleared.update(values)
    return patch.dict(os.environ, cleared, clear=True)


def _applescript_seen_by_osascript(resume_cmd: str) -> str:
    """Run *resume_cmd* through bash with a stub osascript; return its `-e` script."""
    out = subprocess.run(
        ["bash", "-c", f'osascript() {{ printf "%s" "$2"; }}; {resume_cmd}'],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


class TestMacosTerminalApp(unittest.TestCase):
    def test_defaults_to_terminal(self):
        with _env():
            self.assertEqual(macos_terminal_app(), "Terminal")

    def test_iterm_session_picks_iterm(self):
        with _env(TERM_PROGRAM="iTerm.app"):
            self.assertEqual(macos_terminal_app(), "iTerm")

    def test_apple_terminal_session_picks_terminal(self):
        with _env(TERM_PROGRAM="Apple_Terminal"):
            self.assertEqual(macos_terminal_app(), "Terminal")

    def test_env_var_overrides_term_program(self):
        with _env(COZEMPIC_TERMINAL="terminal", TERM_PROGRAM="iTerm.app"):
            self.assertEqual(macos_terminal_app(), "Terminal")
        with _env(COZEMPIC_TERMINAL="iTerm", TERM_PROGRAM="Apple_Terminal"):
            self.assertEqual(macos_terminal_app(), "iTerm")

    def test_env_var_accepts_app_names(self):
        for value in ("iterm2", "iTerm.app", " ITERM "):
            with _env(COZEMPIC_TERMINAL=value):
                self.assertEqual(macos_terminal_app(), "iTerm", value)

    def test_unknown_env_value_falls_back_to_detection(self):
        with _env(COZEMPIC_TERMINAL="kitty", TERM_PROGRAM="iTerm.app"):
            self.assertEqual(macos_terminal_app(), "iTerm")


class TestMacosResumeCommand(unittest.TestCase):
    inner = f"cd {shell_quote('/Users/me/My Project')} && claude --resume abc"

    def test_terminal_script(self):
        with _env():
            script = _applescript_seen_by_osascript(macos_resume_command(self.inner))
        self.assertEqual(
            script,
            'tell application "Terminal" to do script '
            "\"cd '/Users/me/My Project' && claude --resume abc\"",
        )

    def test_iterm_script(self):
        with _env(TERM_PROGRAM="iTerm.app"):
            script = _applescript_seen_by_osascript(macos_resume_command(self.inner))
        self.assertEqual(
            script,
            'tell application "iTerm" to tell current session of (create window with default profile) '
            "to write text \"cd '/Users/me/My Project' && claude --resume abc\"",
        )

    def test_double_quotes_and_backslashes_are_escaped_for_applescript(self):
        with _env():
            script = _applescript_seen_by_osascript(macos_resume_command('echo "a\\b"'))
        self.assertTrue(script.endswith('do script "echo \\"a\\\\b\\""'), script)


_WATCHER_STUBS = (
    "kill() { return 1; }; sleep() { :; }; pgrep() { echo 123; }; date() { echo 0; }; "
    "echo() { :; }; rm() { :; }; osascript() { printf '%s' \"$2\"; }; "
)
_PROJECT = "/Users/me/My Project"
_ITERM_SCRIPT = (
    'tell application "iTerm" to tell current session of (create window with default profile) '
    "to write text \"cd '/Users/me/My Project' && claude --resume sess-mac\""
)


class TestResumeWatchersUseDetectedTerminal(unittest.TestCase):
    def _run_watcher(self, script: str) -> str:
        """Run a captured watcher script with stubbed waits; return what osascript got."""
        out = subprocess.run(["bash", "-c", _WATCHER_STUBS + script], capture_output=True, text=True)
        return out.stdout

    def _capture_popen(self):
        captured = {}

        def fake_popen(cmd_parts, **kwargs):
            captured["script"] = cmd_parts[-1]
            proc = MagicMock()
            proc.pid = 1
            return proc

        return captured, fake_popen

    def test_guard_reload_watcher_opens_iterm(self):
        from cozempic.guard import _spawn_reload_watcher

        captured, fake_popen = self._capture_popen()
        with (
            _env(TERM_PROGRAM="iTerm.app"),
            patch("cozempic.guard.platform.system", return_value="Darwin"),
            patch("cozempic.guard.is_ssh_session", return_value=False),
            patch("cozempic.guard._detect_claude_flags", return_value=""),
            patch("cozempic.guard.subprocess.Popen", side_effect=fake_popen),
        ):
            _spawn_reload_watcher(4242, _PROJECT, session_id="sess-mac")
        self.assertEqual(self._run_watcher(captured["script"]), _ITERM_SCRIPT)

    def test_cli_reload_watcher_opens_iterm(self):
        from cozempic.cli import _spawn_watcher

        captured, fake_popen = self._capture_popen()
        with (
            _env(COZEMPIC_TERMINAL="iterm"),
            patch("cozempic.cli.platform.system", return_value="Darwin"),
            patch("cozempic.cli.is_ssh_session", return_value=False),
            patch("cozempic.guard._detect_claude_flags", return_value=""),
            patch("cozempic.cli.subprocess.Popen", side_effect=fake_popen),
        ):
            _spawn_watcher(4242, _PROJECT, session_id="sess-mac")
        self.assertEqual(self._run_watcher(captured["script"]), _ITERM_SCRIPT)


if __name__ == "__main__":
    unittest.main()
