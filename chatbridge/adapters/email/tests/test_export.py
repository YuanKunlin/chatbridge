"""Offline tests for the email history adapter.

Fake IMAP + temporary spool; no network, no real mailbox, no real credentials.
Run from the chatbridge root:

    python3 -m unittest discover -s adapters/email/tests -p 'test_*.py' -v
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path.home() / "qqmail_router"))

from adapters.email import export  # noqa: E402

core = export.load_core()


def encode_utf7(value: str) -> str:
    """Encode a folder name to IMAP modified UTF-7 (test helper)."""
    out: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        data = "".join(buffer).encode("utf-16-be")
        encoded = base64.b64encode(data).decode("ascii").rstrip("=").replace("/", ",")
        out.append(f"&{encoded}-")
        buffer.clear()

    for char in value:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            out.append("&-" if char == "&" else char)
        else:
            buffer.append(char)
    flush()
    return "".join(out)


def make_mail(
    subject: str,
    body: str,
    *,
    sender: str = "Alice <alice@example.com>",
    to: str = "demo@example.com",
    cc: str = "",
    message_id: str | None = "<msg@example.com>",
    references: str = "",
    in_reply_to: str = "",
    date: str = "Mon, 03 Nov 2026 10:00:00 +0800",
    attachment: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    message["Date"] = date
    if message_id is not None:
        message["Message-ID"] = message_id
    if references:
        message["References"] = references
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    message.set_content(body)
    if attachment:
        message.add_attachment(
            b"ATTACHMENT-SECRET-PAYLOAD",
            maintype="application",
            subtype="octet-stream",
            filename=attachment,
        )
    return message.as_bytes()


class FakeIMAP:
    """Read-only IMAP double: LIST / SELECT / UIDVALIDITY / SEARCH / FETCH."""

    def __init__(self, folders: dict[str, dict[int, bytes]], validity: dict[str, str] | None = None):
        self.folders = folders
        self.validity = validity or {name: "1" for name in folders}
        self.broken: set[tuple[str, int]] = set()
        self.selected: str | None = None
        self.select_log: list[tuple[str, bool]] = []
        self.fetch_calls: list[tuple[str, int]] = []
        self.logged_out = False

    def list(self):
        lines = []
        for raw in self.folders:
            lines.append(f'(\\HasNoChildren) "/" "{raw}"'.encode("utf-8"))
        return "OK", lines

    def select(self, name: str, readonly: bool = False):
        if not readonly:
            raise AssertionError("adapter must select readonly")
        name = name.strip('"')
        self.select_log.append((name, readonly))
        self.selected = name
        return "OK", [str(len(self.folders[name])).encode()]

    def response(self, key: str):
        assert key.upper() == "UIDVALIDITY"
        return "UIDVALIDITY", [self.validity[self.selected or ""].encode()]

    def uid(self, command: str, *args):
        assert self.selected is not None
        if command.lower() == "search":
            criterion = str(args[-1])
            uids = sorted(self.folders[self.selected])
            if criterion.upper().startswith("(SINCE"):
                return "OK", [b" ".join(str(uid).encode() for uid in uids)]
            if criterion.upper().startswith("UID "):
                start = int(criterion.split()[1].split(":")[0])
                found = [uid for uid in uids if uid >= start]
                return "OK", [b" ".join(str(uid).encode() for uid in (found or uids[-1:]))]
            raise AssertionError(f"unexpected criterion {criterion!r}")
        if command.lower() == "fetch":
            uid = int(args[0])
            self.fetch_calls.append((self.selected, uid))
            raw = self.folders[self.selected].get(uid)
            if raw is None or (self.selected, uid) in self.broken:
                return "OK", [None]
            prefix = (
                f'{uid} (INTERNALDATE "03-Nov-2026 10:00:00 +0800" RFC822 {{{len(raw)}}}'
            ).encode()
            return "OK", [(prefix, raw), b")"]
        raise AssertionError(f"unexpected uid command {command!r}")

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


class EmailAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.spool = self.tmp / "spool"
        self.spool.mkdir()
        self.credentials = self.tmp / "credentials.json"
        self.credentials.write_text(
            json.dumps({"email": "demo@example.com", "auth_code": "fake-auth-code"}),
            encoding="utf-8",
        )
        self.folders: dict[str, dict[int, bytes]] = {"INBOX": {}, "Sent Messages": {}}
        self.fake = FakeIMAP(self.folders)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def connect(self, host, port, timeout, user, code):
        self.assertEqual(user, "demo@example.com")
        self.assertEqual(code, "fake-auth-code")
        return self.fake

    def args(self, **overrides) -> argparse.Namespace:
        data = {
            "command": "export",
            "spool": str(self.spool),
            "since": "2026-01-01",
            "folders": "INBOX,Sent",
            "account": "auto",
            "credentials": str(self.credentials),
            "full": False,
            "json": True,
            "dry_run": False,
        }
        data.update(overrides)
        return argparse.Namespace(**data)

    def run_export(self, **overrides) -> dict:
        return export.export(self.args(**overrides), core, connect=self.connect)

    def thread_files(self) -> list[Path]:
        return sorted((self.spool / "email" / "demo@example.com" / "threads").glob("*.md"))

    def read_threads(self) -> list[str]:
        return [path.read_text(encoding="utf-8") for path in self.thread_files()]

    # -- grouping --------------------------------------------------------
    def test_references_group_replies_and_write_contract(self) -> None:
        long_body = "\n".join(f"第 {index} 行：这是很长的邮件正文，不能被截断。" for index in range(200))
        self.folders["INBOX"][1] = make_mail(
            "项目进度与下一步安排",
            long_body,
            message_id="<root@example.com>",
            attachment="项目进度_v3.docx",
        )
        self.folders["INBOX"][2] = make_mail(
            "Re: 项目进度与下一步安排",
            "收到，我按这个顺序推进。",
            sender="demo@example.com",
            to="Alice <alice@example.com>",
            message_id="<reply@example.com>",
            references="<root@example.com>",
            in_reply_to="<root@example.com>",
            date="Mon, 03 Nov 2026 11:00:00 +0800",
        )
        self.folders["INBOX"][3] = make_mail(
            "发票",
            "附件是发票。",
            sender="Billing <billing@example.com>",
            message_id="<invoice@example.com>",
            date="Mon, 03 Nov 2026 12:00:00 +0800",
        )
        self.folders["Sent Messages"][1] = make_mail(
            "Re: 项目进度与下一步安排",
            "补充一句：下周做端到端验收。",
            sender="demo@example.com",
            to="Alice <alice@example.com>",
            message_id="<reply2@example.com>",
            references="<root@example.com>",
            in_reply_to="<reply@example.com>",
            date="Mon, 03 Nov 2026 13:00:00 +0800",
        )

        payload = self.run_export()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["conversations"], 2)
        self.assertEqual(payload["messages"], 4)

        files = self.thread_files()
        self.assertEqual(len(files), 2)
        joined = "\n".join(self.read_threads())
        self.assertIn("第 199 行", joined)  # full body, no truncation
        self.assertIn("📎 项目进度_v3.docx", joined)
        self.assertNotIn("ATTACHMENT-SECRET-PAYLOAD", joined)
        self.assertIn("<!-- mid: root@example.com -->", joined)
        self.assertIn("conversation_id:", joined)
        self.assertIn("kind: thread", joined)
        # front matter fields required by the contract
        for key in ("contract: chat-md/1", "platform: email", "account: demo@example.com"):
            self.assertIn(key, joined)
        # read-only access
        self.assertTrue(all(readonly for _, readonly in self.fake.select_log))
        self.assertEqual([entry[0] for entry in self.fake.select_log], ["INBOX", "Sent Messages"])

    def test_subject_and_participants_fallback(self) -> None:
        self.folders["INBOX"][1] = make_mail("周会安排", "本周三上午十点。", message_id="<a@example.com>")
        self.folders["INBOX"][2] = make_mail(
            "Re: 周会安排",
            "收到。",
            sender="demo@example.com",
            to="Alice <alice@example.com>",
            message_id="<b@example.com>",
            date="Mon, 03 Nov 2026 11:00:00 +0800",
        )
        self.folders["INBOX"][3] = make_mail(
            "另一个话题",
            "完全无关。",
            sender="Bob <bob@example.com>",
            message_id="<c@example.com>",
        )
        payload = self.run_export(folders="INBOX")
        self.assertEqual(payload["conversations"], 2)
        self.assertEqual(payload["messages"], 3)
        texts = self.read_threads()
        conversation_lines = [
            line for text in texts for line in text.splitlines() if line.startswith("conversation:")
        ]
        self.assertIn('conversation: "周会安排"', conversation_lines)

    def test_same_message_in_two_folders_is_deduped(self) -> None:
        raw = make_mail("重复消息", "只应出现一次。", message_id="<same@example.com>")
        self.folders["INBOX"][1] = raw
        self.folders["Sent Messages"][1] = raw
        payload = self.run_export()
        self.assertEqual(payload["messages"], 1)
        text = self.read_threads()[0]
        self.assertEqual(text.count("<!-- mid: same@example.com -->"), 1)

    # -- incrementality --------------------------------------------------
    def test_second_run_does_not_duplicate_or_refetch(self) -> None:
        self.folders["INBOX"][1] = make_mail("增量测试", "第一封。", message_id="<m1@example.com>")
        first = self.run_export()
        before = self.read_threads()
        fetches = len(self.fake.fetch_calls)
        state = json.loads((self.spool / "_state" / "email.json").read_text(encoding="utf-8"))
        self.assertEqual(state["folders"]["INBOX"]["last_uid"], 1)
        self.assertEqual(state["folders"]["Sent"]["last_uid"], 0)

        second = self.run_export()
        self.assertEqual(first["messages"], second["messages"])
        self.assertEqual(len(self.fake.fetch_calls), fetches)
        self.assertEqual(before, self.read_threads())

    def test_uidvalidity_change_refetches_without_duplicates(self) -> None:
        self.folders["INBOX"][1] = make_mail("有效期内", "第一封。", message_id="<m1@example.com>")
        self.run_export()
        before = self.read_threads()
        fetches = len(self.fake.fetch_calls)
        self.fake.validity["INBOX"] = "2"
        payload = self.run_export()
        self.assertEqual(payload["messages"], 1)
        self.assertGreater(len(self.fake.fetch_calls), fetches)
        self.assertEqual(before, self.read_threads())

    def test_new_message_appends_to_existing_thread(self) -> None:
        self.folders["INBOX"][1] = make_mail(
            "线程追加", "根消息。", message_id="<root@x>", date="Mon, 03 Nov 2026 09:00:00 +0800"
        )
        self.run_export()
        self.folders["INBOX"][2] = make_mail(
            "Re: 线程追加",
            "回复消息。",
            message_id="<reply@x>",
            references="<root@x>",
            date="Tue, 04 Nov 2026 09:00:00 +0800",
        )
        payload = self.run_export()
        self.assertEqual(payload["messages"], 2)
        self.assertEqual(payload["conversations"], 1)
        text = self.read_threads()[0]
        self.assertIn("## 2026-11-03", text)
        self.assertIn("## 2026-11-04", text)
        self.assertLess(text.index("根消息"), text.index("回复消息"))

    # -- CLI behaviour ---------------------------------------------------
    def test_dry_run_writes_nothing(self) -> None:
        self.folders["INBOX"][1] = make_mail("只探测", "不写盘。")
        payload = self.run_export(dry_run=True)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["messages"], 0)
        self.assertFalse((self.spool / "email").exists())
        self.assertFalse((self.spool / "_state").exists())
        self.assertFalse((self.spool / export.FRAGMENT_NAME).exists())

    def test_folder_alias_and_utf7(self) -> None:
        raw = encode_utf7("已发送")
        self.assertEqual(core.decode_utf7(raw), "已发送")
        self.folders = {"INBOX": {1: make_mail("别名", "body", message_id="<a@x>")}, raw: {}}
        self.fake = FakeIMAP(self.folders)
        payload = self.run_export(folders="INBOX,Sent")
        self.assertEqual(payload["folders"][1]["raw"], raw)
        self.assertEqual(payload["folders"][1]["display"], "已发送")
        state = json.loads((self.spool / "_state" / "email.json").read_text(encoding="utf-8"))
        self.assertIn("Sent", state["folders"])

    def test_manifest_counts_match_validator(self) -> None:
        self.folders["INBOX"][1] = make_mail("验收", "validator 测试。", message_id="<v1@x>")
        self.folders["INBOX"][2] = make_mail(
            "Re: 验收",
            "第二封。",
            message_id="<v2@x>",
            references="<v1@x>",
            date="Mon, 03 Nov 2026 11:00:00 +0800",
        )
        payload = self.run_export()
        self.assertEqual(payload["status"], "ok")
        fragment = json.loads((self.spool / export.FRAGMENT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(fragment["contract"], "aimem-spool/1")
        self.assertEqual(fragment["source"], "email")
        self.assertEqual(fragment["accounts"][0]["id"], "demo@example.com")
        self.assertEqual(fragment["conversations"], payload["conversations"])
        self.assertEqual(fragment["messages"], payload["messages"])

        manifest = {
            "contract": "aimem-spool/1",
            "generated_at": "2026-11-04T15:00:00+08:00",
            "sources": {"email": fragment},
        }
        (self.spool / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        proc = subprocess.run(
            [sys.executable, str(ROOT / "core" / "validate_spool.py"), str(self.spool), "--strict"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_parser_defaults(self) -> None:
        parser = export.build_parser()
        args = parser.parse_args(["export", "--spool", "x"])
        self.assertEqual(args.folders, "INBOX,Sent")
        self.assertFalse(args.full)
        self.assertFalse(args.dry_run)

    def test_noop_run_keeps_fragment_and_state_bytes(self) -> None:
        self.folders["INBOX"][1] = make_mail("无变化", "只导一次。", message_id="<same@x>")
        self.run_export()
        fragment = (self.spool / export.FRAGMENT_NAME).read_bytes()
        state = (self.spool / "_state" / "email.json").read_bytes()
        second = self.run_export()
        self.assertTrue(second.get("unchanged"))
        self.assertEqual(fragment, (self.spool / export.FRAGMENT_NAME).read_bytes())
        self.assertEqual(state, (self.spool / "_state" / "email.json").read_bytes())

    def test_backfill_root_adopts_existing_reply_thread(self) -> None:
        self.folders["INBOX"][2] = make_mail(
            "Re: 回填线程",
            "先到的回复。",
            message_id="<reply@x>",
            references="<root@x>",
            date="Tue, 04 Nov 2026 09:00:00 +0800",
        )
        first = self.run_export()
        self.assertEqual(first["conversations"], 1)
        # root arrives later with a new UID, no References header
        self.folders["INBOX"][3] = make_mail(
            "回填线程",
            "后补的根消息。",
            message_id="<root@x>",
            date="Mon, 03 Nov 2026 09:00:00 +0800",
        )
        second = self.run_export()
        self.assertEqual(second["conversations"], 1)
        self.assertEqual(second["messages"], 2)

    def test_partial_status_when_a_fetch_fails(self) -> None:
        self.folders["INBOX"][1] = make_mail("完整", "可导出。", message_id="<ok@x>")
        self.folders["INBOX"][2] = make_mail("坏消息", "fetch 为空。", message_id="<bad@x>")
        self.fake.broken.add(("INBOX", 2))
        payload = self.run_export()
        self.assertEqual(payload["status"], "partial")
        self.assertTrue(payload["errors"])
        self.assertEqual(payload["messages"], 1)

    def test_synthetic_message_id_dedupes_across_folders(self) -> None:
        raw = make_mail("无 Message-ID", "同一封。", message_id=None)
        self.folders["INBOX"][1] = raw
        self.folders["Sent Messages"][1] = raw
        payload = self.run_export()
        self.assertEqual(payload["messages"], 1)
        text = self.read_threads()[0]
        self.assertIn("<!-- mid: synth:", text)

    def test_parse_thread_roundtrip(self) -> None:
        thread = {
            "account": "demo@example.com",
            "conversation": "往返",
            "conversation_id": "demo@example.com:<m@x>",
        }
        record = export.Record(
            mid="<m@x>",
            dt=core.now().replace(year=2026, month=11, day=3, hour=9, minute=0, second=0, microsecond=0),
            sender="Alice",
            subject="往返",
            body_lines=["第一行", "", "第三行", "📎 a.pdf"],
        )
        path = self.tmp / "thread.md"
        export.atomic_write(path, export.render_thread(thread, [record], "2026-11-04T15:00:00+08:00"))
        parsed = export.parse_thread(path, core.TZ)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].mid, "<m@x>")
        self.assertEqual(parsed[0].body_lines, ["第一行", "", "第三行", "📎 a.pdf"])


if __name__ == "__main__":
    unittest.main()
