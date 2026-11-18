"""Page-level SQLCipher4 decryption, used when the sqlcipher3 binding is absent.

Windows Python has no sqlcipher3 wheel, so the adapter decrypts pages itself.
Layout: page 1 = salt(16) + ciphertext(4000) + IV(16) + HMAC(64); other pages
are 4016 bytes of ciphertext plus the same 80-byte reserve.  Plaintext pages
are padded back to 4096 with zeroes; SQLite keeps the reserve declared in the
header and accepts that.

WAL frames are decrypted and applied over the main file, mirroring what SQLite
would read.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE = 4096
RESERVE = 80


def verify_key(key: bytes, page1: bytes) -> bool:
  """SQLCipher4 page-1 HMAC check (2-iteration MAC key derivation)."""
  if len(page1) < PAGE:
    return False
  mac_salt = bytes(b ^ 0x3A for b in page1[:16])
  mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, dklen=32)
  mac = hmac.new(mac_key, page1[16:PAGE - RESERVE + 16], hashlib.sha512)
  mac.update(struct.pack("<I", 1))
  return mac.digest() == page1[PAGE - 64:PAGE]


def _page_plain(page: bytes, key: bytes, pgno: int) -> bytes:
  iv = page[PAGE - RESERVE:PAGE - RESERVE + 16]
  if pgno == 1:
    ct = page[16:PAGE - RESERVE]
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    pt = b"SQLite format 3\x00" + dec.update(ct) + dec.finalize()
  else:
    ct = page[:PAGE - RESERVE]
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    pt = dec.update(ct) + dec.finalize()
  return pt + b"\x00" * RESERVE


def _wal_pages(src: Path, key: bytes) -> dict[int, bytes]:
  wal = Path(str(src) + "-wal")
  if not wal.exists():
    return {}
  data = wal.read_bytes()
  if len(data) < 32:
    return {}
  if struct.unpack_from(">I", data, 0)[0] not in (0x377F0682, 0x377F0683):
    return {}
  if struct.unpack_from(">I", data, 8)[0] != PAGE:
    return {}
  salt = data[16:24]
  pages: dict[int, bytes] = {}
  off = 32
  while off + 24 + PAGE <= len(data):
    hdr = data[off:off + 24]
    pgno = struct.unpack_from(">I", hdr, 0)[0]
    if hdr[8:16] != salt:
      break
    if pgno > 0:
      pages[pgno] = _page_plain(data[off + 24:off + 24 + PAGE], key, pgno)
    off += 24 + PAGE
  return pages


def decrypt(src: Path, dst: Path, key: bytes) -> int:
  """Decrypt one database; returns the number of WAL pages applied."""
  data = src.read_bytes()
  if len(data) % PAGE:
    raise ValueError(f"{src.name}: not page aligned")
  frames = _wal_pages(src, key)
  pages = {i + 1: _page_plain(data[i * PAGE:(i + 1) * PAGE], key, i + 1)
           for i in range(len(data) // PAGE)}
  pages.update(frames)
  total = max(pages) if pages else 0
  out = bytearray()
  for pgno in range(1, total + 1):
    out += pages.get(pgno, b"\x00" * PAGE)
  dst.parent.mkdir(parents=True, exist_ok=True)
  dst.write_bytes(out)
  return len(frames)
