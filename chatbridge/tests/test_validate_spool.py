from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "core" / "validate_spool.py"
SAMPLE = ROOT / "spool-dev" / "sample"


def run(spool: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(spool), "--strict"],
        capture_output=True,
        text=True,
    )


class ValidateSpoolTest(unittest.TestCase):
    def test_sample_passes(self) -> None:
        r = run(SAMPLE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_missing_front_matter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wechat" / "acc" / "private").mkdir(parents=True)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "contract": "aimem-spool/1",
                        "sources": {
                            "wechat": {
                                "root": "wechat",
                                "accounts": [{"id": "acc"}],
                                "messages": 1,
                                "conversations": 1,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "wechat" / "acc" / "private" / "bad.md").write_text(
                "# no front matter\n\n## 2025-01-01\n\n**10:00:00 · A**\n\n> hi\n",
                encoding="utf-8",
            )
            r = run(root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("missing front matter", r.stdout)


    def test_standalone_mid_comment_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "email" / "acc" / "threads").mkdir(parents=True)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "contract": "aimem-spool/1",
                        "sources": {
                            "email": {
                                "root": "email",
                                "accounts": [{"id": "acc"}],
                                "messages": 1,
                                "conversations": 1,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "email" / "acc" / "threads" / "t.md").write_text(
                "---\ncontract: chat-md/1\nplatform: email\naccount: acc\n"
                "kind: thread\nconversation: \"t\"\nconversation_id: \"t1\"\n---\n"
                "# t\n\n## 2026-01-01\n\n**10:00:00 · A**\n\n"
                "<!-- mid: x@example.com -->\n> body\n",
                encoding="utf-8",
            )
            r = run(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
