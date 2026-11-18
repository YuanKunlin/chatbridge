"""Email history adapter: IMAP -> chat-md/1 threads.

Contract: core/CONTRACT.md sections 4, 5 and 6.3.

Design notes:
  * IMAP is opened read-only; folders are LISTed and decoded (modified UTF-7)
    before SELECT, so INBOX/Sent aliases work across QQ/Dovecot names.
  * Incremental state is per folder (UIDVALIDITY + last_uid) and per thread
    (Message-ID index).  Re-runs never duplicate a message: the Markdown files
    are themselves parsed and merged by their ``<!-- mid: … -->`` markers.
  * Attachments are name-only (``📎 name``); bodies are written in full.
  * The adapter writes only ``<spool>/email/<account>/``, the fragment and
    ``_state/email.json``.  It never touches manifest.json, DSH or the router's
    PAUSED/loop state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from . import __version__
except ImportError:  # running the file directly
    __version__ = "1.0"

CONTRACT = "aimem-spool/1"
SOURCE = "email"
ADAPTER = "imap"
VERSION = "1.0"
FRAGMENT_NAME = ".manifest.email.json"
STATE_NAME = "email.json"
DEFAULT_FOLDERS = "INBOX,Sent"
DEFAULT_DAYS = 365
ACCOUNT_RE = re.compile(r"[^A-Za-z0-9._@+-]+")
DATE_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
MSG_RE = re.compile(r"^\*\*(\d{1,2}:\d{2}(?::\d{2})?)\s*[·|]\s*(.+?)\*\*\s*$")
MID_RE = re.compile(r"^<!--\s*mid:\s*(.+?)\s*-->\s*$")
BODY_MID_RE = re.compile(r"^>\s*<!--\s*mid:\s*(.+?)\s*-->\s*$")
SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:(?:re|fw|fwd|aw|sv|回复|答复|转发)\s*[:：]\s*)+",
    re.IGNORECASE,
)


class AdapterError(RuntimeError):
    def __init__(self, code: int, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class Record:
    """One message rendered into a thread."""

    mid: str
    dt: datetime
    sender: str
    subject: str
    body_lines: list[str]
    attachments: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    participants: str = ""
    uid: int = 0
    folder: str = ""


def log(message: str, core) -> None:
    """Stderr diagnostics with credential/address redaction, never bodies."""
    stamp = datetime.now(core.TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {core.redact(message)}", file=sys.stderr, flush=True)


def load_core():
    """Import mail_core from ~/qqmail_router (override with QQMAIL_ROUTER_DIR)."""
    try:
        import mail_core as module

        return module
    except ImportError:
        pass
    candidates: list[Path] = []
    env = os.environ.get("QQMAIL_ROUTER_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path.home() / "qqmail_router")
    try:
        candidates.append(Path(__file__).resolve().parents[3] / "qqmail_router")
    except IndexError:
        pass
    for base in candidates:
        if (base / "mail_core.py").is_file():
            sys.path.insert(0, str(base))
            import mail_core as module

            return module
    raise AdapterError(3, "mail_core.py not found; set QQMAIL_ROUTER_DIR to ~/qqmail_router")


def normalize_subject(value: str) -> str:
    text = SUBJECT_PREFIX_RE.sub("", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def participant_key(from_addr: str, to: str, cc: str, account: str, core) -> str:
    """Stable correspondent set for subject-only threading (self removed)."""
    addresses = {addr.casefold() for _, addr in core.parse_addresses(from_addr) if addr}
    for value in (to, cc):
        for _, addr in core.parse_addresses(value):
            if addr:
                addresses.add(addr.casefold())
    addresses.discard(account.casefold())
    if not addresses:
        return (from_addr or "unknown").casefold()
    return ",".join(sorted(addresses))


def message_identity(mail, core) -> str:
    if mail.message_id_header:
        return mail.message_id_header.strip().strip("<>").strip()
    basis = "\x1f".join(
        [
            mail.from_addr or "",
            mail.date or "",
            mail.subject or "",
            mail.body[:500],
        ]
    )
    return "synth:" + hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()


def thread_key_for(record: Record, index: dict[str, str]) -> str:
    for ref in record.references:
        known = index.get(ref)
        if known:
            return known
    if record.references:
        return f"root:{record.references[0]}"
    digest = hashlib.sha1(
        f"{normalize_subject(record.subject).casefold()}\x1f{record.participants}".encode("utf-8")
    ).hexdigest()[:16]
    return f"subject:{digest}"


def conversation_id_for(account: str, thread_key: str) -> str:
    if thread_key.startswith("root:"):
        return f"{account}:{thread_key[5:]}"
    return f"{account}:{thread_key}"


def thread_file_for(conversation_id: str) -> str:
    digest = hashlib.sha1(conversation_id.encode("utf-8")).hexdigest()[:12]
    return f"threads/thread-{digest}.md"


def _body_lines(mail) -> list[str]:
    lines = [line.rstrip("\r") for line in (mail.body or "").split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["[空]"]
    return lines


def make_record(mail, dt: datetime, core, folder: str, account: str) -> Record:
    refs: list[str] = []
    for value in (mail.references, mail.in_reply_to):
        for item in core.parse_msg_ids(value):
            if item not in refs:
                refs.append(item)
    lines = _body_lines(mail)
    for name in mail.attachments:
        lines.append(f"📎 {name}")
    sender = mail.from_name or mail.from_addr or "未知发件人"
    sender = re.sub(r"[\r\n]+", " ", sender).strip() or "未知发件人"
    return Record(
        mid=message_identity(mail, core),
        dt=dt,
        sender=sender,
        subject=mail.subject,
        body_lines=lines,
        attachments=list(mail.attachments),
        references=refs,
        participants=participant_key(mail.from_addr, mail.to, mail.cc, account, core),
        uid=mail.uid,
        folder=folder,
    )


def parse_thread(path: Path, tz=None) -> list[Record]:
    """Parse one of our own thread files back into records (for merge/dedupe)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if tz is None:
        tz = datetime.now().astimezone().tzinfo
    records: list[Record] = []
    current_date: date | None = None
    current: Record | None = None
    pending_mid = ""
    for line in text.splitlines():
        match = DATE_RE.match(line)
        if match:
            try:
                current_date = date.fromisoformat(match.group(1))
            except ValueError:
                current_date = None
            continue
        match = MID_RE.match(line)
        if match:
            pending_mid = match.group(1)
            continue
        body_mid = BODY_MID_RE.match(line)
        if body_mid and current is not None:
            current.mid = body_mid.group(1)
            continue
        match = MSG_RE.match(line)
        if match:
            clock = match.group(1)
            if clock.count(":") == 2:
                hour, minute, second = (int(part) for part in clock.split(":"))
            else:
                hour, minute = (int(part) for part in clock.split(":"))
                second = 0
            base = current_date or date(1970, 1, 1)
            dt = datetime(base.year, base.month, base.day, hour, minute, second, tzinfo=tz)
            current = Record(
                mid=pending_mid or f"parsed:{len(records)}",
                dt=dt,
                sender=match.group(2),
                subject="",
                body_lines=[],
            )
            pending_mid = ""
            records.append(current)
            continue
        if current is not None and line.startswith("> "):
            current.body_lines.append(line[2:])
    return records


def render_thread(thread: dict, records: list[Record], exported_at: str) -> str:
    conversation = thread.get("conversation") or "（无主题）"
    lines = [
        "---",
        "contract: chat-md/1",
        "platform: email",
        f"account: {thread['account']}",
        "kind: thread",
        f"conversation: {json.dumps(conversation, ensure_ascii=False)}",
        f"conversation_id: {json.dumps(thread['conversation_id'], ensure_ascii=False)}",
        f"exported_at: {json.dumps(exported_at, ensure_ascii=False)}",
        "---",
        f"# {conversation}",
        "",
    ]
    current_date = None
    for record in sorted(records, key=lambda item: (item.dt, item.mid)):
        day = record.dt.strftime("%Y-%m-%d")
        if day != current_date:
            if current_date is not None:
                lines.append("")
            lines.append(f"## {day}")
            lines.append("")
            current_date = day
        lines.append(f"**{record.dt.strftime('%H:%M:%S')} · {record.sender}**")
        lines.append("")
        lines.append(f"> <!-- mid: {record.mid} -->")
        for body_line in record.body_lines or ["[空]"]:
            lines.append(f"> {body_line}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(text.encode("utf-8"))
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def atomic_write_json(path: Path, payload: dict, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary.write_bytes(data.encode("utf-8"))
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def load_state(path: Path, account: str) -> dict:
    state = {
        "contract": CONTRACT,
        "source": SOURCE,
        "adapter": ADAPTER,
        "account": account,
        "since": "",
        "updated_at": "",
        "folders": {},
        "threads": {},
    }
    if not path.exists():
        return state
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    if not isinstance(data, dict) or data.get("source") != SOURCE or data.get("account") != account:
        return state
    for key in ("folders", "threads"):
        if isinstance(data.get(key), dict):
            state[key] = data[key]
    if data.get("since"):
        state["since"] = str(data["since"])
    return state


def thread_index(state: dict) -> dict[str, str]:
    index: dict[str, str] = {}
    for key, thread in state["threads"].items():
        for mid in thread.get("messages", []):
            index[mid] = key
    return index


def assign_threads(records: list[Record], state: dict, account: str) -> dict[str, str]:
    """Attach records to threads; returns {message_id: thread_key}."""
    index = thread_index(state)
    assigned: dict[str, str] = {}
    for record in records:
        key = thread_key_for(record, index)
        # Backfill case: an earlier reply created ``root:<id>`` before the root
        # itself was fetched; adopt that thread when the root arrives later.
        own_root = f"root:{record.mid}"
        if key.startswith("subject:") and own_root in state["threads"]:
            key = own_root
        thread = state["threads"].get(key)
        if thread is None:
            conversation_id = conversation_id_for(account, key)
            conversation = normalize_subject(record.subject) or "（无主题）"
            thread = {
                "account": account,
                "conversation": conversation,
                "conversation_id": conversation_id,
                "file": thread_file_for(conversation_id),
                "messages": [],
            }
            state["threads"][key] = thread
        if not thread.get("conversation"):
            thread["conversation"] = normalize_subject(record.subject) or "（无主题）"
        if record.mid not in thread["messages"]:
            thread["messages"].append(record.mid)
        index[record.mid] = key
        assigned[record.mid] = key
    return assigned


def merge_thread(
    thread: dict,
    records: list[Record],
    account_root: Path,
    exported_at: str,
    tz=None,
) -> bool:
    path = account_root / thread["file"]
    existing = parse_thread(path, tz) if path.exists() else []
    by_mid: dict[str, Record] = {record.mid: record for record in existing}
    for record in records:
        if record.mid not in by_mid:
            by_mid[record.mid] = record
    merged = sorted(by_mid.values(), key=lambda item: (item.dt, item.mid))
    thread["messages"] = [record.mid for record in merged]
    if path.exists() and len(merged) == len(existing):
        return False
    atomic_write(path, render_thread(thread, merged, exported_at))
    return True


def scan_counts(threads_dir: Path) -> tuple[int, int]:
    conversations = 0
    messages = 0
    if not threads_dir.is_dir():
        return 0, 0
    for path in sorted(threads_dir.glob("*.md")):
        if path.name.lower() == "index.md" or path.name.lower().endswith("-all.md"):
            continue
        conversations += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        messages += sum(1 for line in text.splitlines() if MSG_RE.match(line))
    return conversations, messages


def reconcile_orphans(state: dict, account_root: Path, tz=None) -> None:
    """Adopt thread files the state does not know about (state loss / manual copy)."""
    threads_dir = account_root / "threads"
    if not threads_dir.is_dir():
        return
    known = {thread.get("file") for thread in state["threads"].values()}
    for path in sorted(threads_dir.glob("*.md")):
        relative = path.relative_to(account_root).as_posix()
        if relative in known:
            continue
        parsed = parse_thread(path, tz)
        if not parsed:
            continue
        text = path.read_text(encoding="utf-8")
        conversation = "（无主题）"
        conversation_id = f"{state['account']}:{path.stem}"
        for line in text.splitlines():
            if line.startswith("conversation: "):
                conversation = line[len("conversation: ") :].strip().strip('"')
            elif line.startswith("conversation_id: "):
                conversation_id = line[len("conversation_id: ") :].strip().strip('"')
            elif line.startswith("# "):
                break
        key = conversation_id.split(":", 1)[1] if ":" in conversation_id else conversation_id
        state["threads"][f"adopted:{key}"] = {
            "account": state["account"],
            "conversation": conversation,
            "conversation_id": conversation_id,
            "file": relative,
            "messages": [record.mid for record in parsed],
        }
        known.add(relative)


def _parse_since(value: str | None, fallback: str | None, core) -> date:
    text = value or fallback
    if text:
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise AdapterError(3, f"invalid --since {text!r}; expected YYYY-MM-DD")
    return (core.now() - timedelta(days=DEFAULT_DAYS)).date()


def _split_folders(value: str) -> list[str]:
    return [item.strip() for item in (value or DEFAULT_FOLDERS).split(",") if item.strip()]


def load_credentials(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AdapterError(3, f"credentials missing: {path}", details={"path": str(path)})
    except (OSError, ValueError) as error:
        raise AdapterError(3, f"credentials unreadable: {error}", details={"path": str(path)})
    email_address = str(data.get("email") or "").strip()
    auth_code = str(data.get("auth_code") or "").strip()
    if not email_address or not auth_code:
        raise AdapterError(3, "credentials need email and auth_code", details={"path": str(path)})
    return {"email": email_address, "auth_code": auth_code}


def sanitize_account(value: str) -> str:
    account = ACCOUNT_RE.sub("_", value or "").strip("._")
    if not account:
        raise AdapterError(3, "empty --account")
    return account


def collect(
    args: argparse.Namespace,
    core,
    *,
    connect=None,
) -> tuple[list[Record], list[str], dict, dict]:
    """Connect, discover folders and fetch new messages.

    Returns (records, errors, state, meta).  Raises AdapterError for setup
    failures; per-message failures are collected in ``errors``.
    """
    credentials_path = Path(
        args.credentials
        or os.environ.get("QQMAIL_CREDENTIALS")
        or (Path.home() / ".config/qqmail/credentials.json")
    ).expanduser()
    credentials = load_credentials(credentials_path)
    core.register_secret(credentials["auth_code"])

    account = sanitize_account(args.account if args.account != "auto" else credentials["email"])
    spool = Path(args.spool).expanduser().resolve()
    state_path = spool / "_state" / STATE_NAME
    state = load_state(state_path, account)

    state_before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    since = _parse_since(args.since, state.get("since") or None, core)
    since_text = since.isoformat()
    full = bool(args.full or (args.since and args.since != state.get("since")) or state.get("since") != since_text)

    host = os.environ.get("QQMAIL_IMAP_HOST", "imap.qq.com")
    port = int(os.environ.get("QQMAIL_IMAP_PORT", "993"))
    timeout = int(os.environ.get("QQMAIL_IMAP_TIMEOUT", "30"))
    connector = connect or core.connect

    records: list[Record] = []
    errors: list[str] = []
    meta = {
        "account": account,
        "spool": spool,
        "state_path": state_path,
        "since": since_text,
        "full": full,
        "folders": [],
        "fetched": 0,
        "skipped": 0,
        "changed": False,
    }
    mailbox = connector(host, port, timeout, credentials["email"], credentials["auth_code"])
    try:
        discovered = core.list_folders(mailbox)
        requested = _split_folders(args.folders)
        try:
            folders = core.resolve_folders(requested, discovered)
        except core.FolderNotFound as error:
            raise AdapterError(3, str(error), details={"available": error.available})
        if not folders:
            raise AdapterError(3, "no folders requested", details={"available": [f.display for f in discovered]})
        meta["folders"] = [{"key": f.key, "raw": f.raw, "display": f.display} for f in folders]

        seen: set[str] = set()
        index = thread_index(state)
        for folder in folders:
            core.select_readonly(mailbox, folder.raw)
            validity = core.uidvalidity(mailbox) or ""
            previous = state["folders"].get(folder.key) or {}
            reset = bool(full or not previous or previous.get("uidvalidity") != validity)
            last_uid = 0 if reset else int(previous.get("last_uid") or 0)
            criterion = f"(SINCE {core.imap_date(since)})" if reset else f"UID {last_uid + 1}:*"
            try:
                uids = sorted(set(core.uids(mailbox, criterion)))
            except Exception as error:  # noqa: BLE001 - record and keep other folders
                errors.append(core.redact(f"{folder.key}: search failed: {type(error).__name__}: {error}"))
                continue
            if not reset:
                uids = [uid for uid in uids if uid > last_uid]
            log(f"{folder.key} ({folder.display}): uidvalidity={validity or '?'} uids={len(uids)}", core)

            folder_last = last_uid
            for uid in uids:
                try:
                    raw, internal = core.fetch_raw_with_date(mailbox, uid)
                    if not raw:
                        errors.append(f"{folder.key} uid {uid}: empty fetch")
                        folder_last = max(folder_last, uid)
                        continue
                    mail = core.parse_mail(uid, raw, full_body=True)
                    dt = core.parse_date(mail.date) or internal or core.now()
                    record = make_record(mail, dt, core, folder.key, account)
                except Exception as error:  # noqa: BLE001
                    errors.append(core.redact(f"{folder.key} uid {uid}: {type(error).__name__}: {error}"))
                    folder_last = max(folder_last, uid)
                    continue
                if record.mid in seen or record.mid in index:
                    meta["skipped"] += 1
                    folder_last = max(folder_last, uid)
                    continue
                seen.add(record.mid)
                records.append(record)
                folder_last = max(folder_last, uid)
                meta["fetched"] += 1

            state["folders"][folder.key] = {
                "raw": folder.raw,
                "display": folder.display,
                "uidvalidity": validity,
                "last_uid": folder_last,
            }
            state["since"] = since_text
        meta["changed"] = bool(records) or (
            json.dumps(state, ensure_ascii=False, sort_keys=True) != state_before
        )
    finally:
        try:
            mailbox.logout()
        except Exception:
            pass
    return records, errors, state, meta


def export(
    args: argparse.Namespace,
    core,
    *,
    connect=None,
) -> dict:
    spool = Path(args.spool).expanduser().resolve()
    records, errors, state, meta = collect(args, core, connect=connect)
    account = meta["account"]
    account_root = spool / "email" / account
    exported_at = core.iso()

    if args.dry_run:
        return {
            "contract": CONTRACT,
            "source": SOURCE,
            "status": "ok" if not errors else "partial",
            "accounts": [account],
            "conversations": 0,
            "messages": 0,
            "fragment": FRAGMENT_NAME,
            "errors": errors,
            "dry_run": True,
            "folders": meta["folders"],
            "messages_seen": len(records),
        }

    fragment_path = spool / FRAGMENT_NAME
    state_path = spool / "_state" / STATE_NAME
    if (
        not records
        and not errors
        and not meta.get("changed")
        and not getattr(args, "full", False)
        and fragment_path.exists()
        and state_path.exists()
    ):
        conversations, messages = scan_counts(account_root / "threads")
        log("no changes: fragment/state kept as-is", core)
        return {
            "contract": CONTRACT,
            "source": SOURCE,
            "status": "ok",
            "accounts": [account],
            "conversations": conversations,
            "messages": messages,
            "fragment": FRAGMENT_NAME,
            "errors": [],
            "folders": meta["folders"],
            "new_messages": 0,
            "skipped": meta["skipped"],
            "unchanged": True,
        }

    records.sort(key=lambda item: (item.dt, item.mid))
    assigned = assign_threads(records, state, account)
    by_thread: dict[str, list[Record]] = {}
    for record in records:
        by_thread.setdefault(assigned[record.mid], []).append(record)
    for key, thread_records in by_thread.items():
        merge_thread(state["threads"][key], thread_records, account_root, exported_at, core.TZ)

    reconcile_orphans(state, account_root, core.TZ)
    state["updated_at"] = exported_at
    state["since"] = meta["since"]
    atomic_write_json(spool / "_state" / STATE_NAME, state, mode=0o600)

    conversations, messages = scan_counts(account_root / "threads")
    status = "ok" if not errors else "partial"
    fragment = {
        "contract": CONTRACT,
        "source": SOURCE,
        "adapter": ADAPTER,
        "version": VERSION,
        "status": status,
        "complete": not errors,
        "root": "email",
        "accounts": [
            {"id": account, "label": "QQ 邮箱", "conversations": conversations, "messages": messages}
        ],
        "conversations": conversations,
        "messages": messages,
        "last_run": exported_at,
        "errors": errors,
    }
    atomic_write_json(spool / FRAGMENT_NAME, fragment)

    log(
        f"exported account={core.mask_email(account)} folders={len(meta['folders'])} "
        f"new={len(records)} skipped={meta['skipped']} threads={conversations} messages={messages}",
        core,
    )
    log(f"fragment={FRAGMENT_NAME} state={STATE_NAME}", core)
    return {
        "contract": CONTRACT,
        "source": SOURCE,
        "status": status,
        "accounts": [account],
        "conversations": conversations,
        "messages": messages,
        "fragment": FRAGMENT_NAME,
        "errors": errors,
        "folders": meta["folders"],
        "new_messages": len(records),
        "skipped": meta["skipped"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m adapters.email")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("export", help="export email threads into the spool")
    command.add_argument("--spool", required=True, help="spool root (D:\\aimem-spool)")
    command.add_argument("--since", default=None, help="start date YYYY-MM-DD (default: 365 days)")
    command.add_argument("--folders", default=DEFAULT_FOLDERS, help="comma separated folders")
    command.add_argument("--account", default="auto", help="account id or auto")
    command.add_argument("--credentials", default=None, help="credentials json path")
    command.add_argument("--full", action="store_true", help="ignore last_uid and rescan")
    command.add_argument("--json", action="store_true", help="print a machine readable result")
    command.add_argument("--dry-run", action="store_true", help="probe only, write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    want_json = bool(getattr(args, "json", False))
    try:
        core = load_core()
        if args.command != "export":
            raise AdapterError(3, f"unknown command {args.command!r}")
        payload = export(args, core)
        status = payload.get("status", "ok")
        exit_code = 0 if status == "ok" else 4
        if want_json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(
                f"email: status={status} accounts={len(payload['accounts'])} "
                f"threads={payload['conversations']} messages={payload['messages']}"
            )
            for error in payload.get("errors", []):
                print(f"  ERR {error}", file=sys.stderr)
        return exit_code
    except AdapterError as error:
        payload = {
            "contract": CONTRACT,
            "source": SOURCE,
            "status": "error",
            "accounts": [],
            "conversations": 0,
            "messages": 0,
            "fragment": FRAGMENT_NAME,
            "errors": [str(error)],
        }
        if want_json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"email: error: {error}", file=sys.stderr)
        return error.code
    except Exception as error:  # noqa: BLE001 - never leak a traceback with data
        payload = {
            "contract": CONTRACT,
            "source": SOURCE,
            "status": "error",
            "accounts": [],
            "conversations": 0,
            "messages": 0,
            "fragment": FRAGMENT_NAME,
            "errors": [f"{type(error).__name__}: {error}"],
        }
        if want_json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"email: error: {type(error).__name__}: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
