"""A line over MAX_LINE_BYTES is never parsed, but it must never be dropped either.

The loader used to skip such lines, so a prune's save rewrote the transcript
without them: the record vanished from the live session and its child's
parentUuid pointed at nothing. They are now kept as opaque `_parse_error`
wrappers, which save_messages writes back verbatim and no strategy prunes.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cozempic.executor import run_prescription
from cozempic.registry import PRESCRIPTIONS
from cozempic.session import load_messages, load_messages_and_snapshot, save_messages

LIMIT = 2_000


def _records() -> list[dict]:
    big_text = "x" * (LIMIT * 3)
    return [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "read the file"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/f"}}]}},
        {"type": "user", "uuid": "u2", "parentUuid": "a1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": big_text}]}},
        {"type": "assistant", "uuid": "a2", "parentUuid": "u2",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
        {"type": "user", "uuid": "u3", "parentUuid": "a2",
         "message": {"role": "user", "content": "thanks"}},
    ]


class TestOversizedLinesKept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "session.jsonl"
        # Compact separators, as Claude Code writes them, so a no-op save is byte-identical.
        self.lines = [json.dumps(r, separators=(",", ":")) for r in _records()]
        self.path.write_text("".join(line + "\n" for line in self.lines))
        self.oversized = self.lines[2]
        assert len(self.oversized) > LIMIT
        patcher = patch("cozempic.session.MAX_LINE_BYTES", LIMIT)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_loaded_as_opaque_wrapper(self):
        messages = load_messages(self.path)
        self.assertEqual(len(messages), len(self.lines))
        _, msg, size = messages[2]
        self.assertTrue(msg.get("_parse_error"))
        self.assertEqual(msg["_raw"], self.oversized)
        self.assertEqual(size, len(self.oversized.encode()))

    def test_load_save_roundtrip_is_byte_identical(self):
        before = self.path.read_bytes()
        save_messages(self.path, load_messages(self.path), create_backup=False)
        after = self.path.read_bytes()
        self.assertTrue(after == before, f"{len(before)} bytes before, {len(after)} after save")

    def test_every_prescription_keeps_the_line(self):
        for name, strategies in PRESCRIPTIONS.items():
            with self.subTest(prescription=name):
                messages, snapshot = load_messages_and_snapshot(self.path)
                pruned, _ = run_prescription(messages, strategies, {})
                save_messages(self.path, pruned, create_backup=False, snapshot=snapshot)
                saved = self.path.read_text().splitlines()
                self.assertTrue(self.oversized in saved, f"oversized line missing after {name} prune")
                # The oversized record's child still points at it: nothing relinked
                # the chain around the record.
                by_uuid = {json.loads(line).get("uuid"): json.loads(line) for line in saved}
                self.assertIn("u2", by_uuid)
                self.assertEqual(by_uuid["a2"]["parentUuid"], "u2")


if __name__ == "__main__":
    unittest.main()
