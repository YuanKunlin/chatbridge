"""Image branch: synthetic V2 .dat -> decode_images -> link in Markdown.

This proves the wiring without needing the live client's per-session image key.
"""
from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from adapters.wechat import pipeline
from adapters.wechat.tests import fixtures

GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
PNG = bytes.fromhex(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
  "890000000a49444154789c6360000002000154a24f610000000049454e44ae426082")
KEY = b"0123456789abcdef"


def build_v2(plain: bytes) -> bytes:
  """Wrap plaintext in the V2 container the decoder expects."""
  pad = 16 - len(plain) % 16
  padded = plain + bytes([pad]) * pad
  aes_size = len(plain)
  aligned = aes_size + (16 - aes_size % 16)
  enc = Cipher(algorithms.AES(KEY), modes.ECB()).encryptor()
  cipher = enc.update(padded[:aligned]) + enc.finalize()
  header = b"\x07\x08\x56\x32\x08\x07" + struct.pack("<LL", aes_size, 0) + b"\x00"
  return header + cipher


class ImageDecodeTest(unittest.TestCase):
  def test_synthetic_v2_turns_into_png_link(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      work = Path(tmp)
      dat = work / "demo.dat"
      dat.write_bytes(build_v2(PNG))
      md5 = fixtures.md5("img")
      index = work / "media-index.jsonl"
      index.write_text(json.dumps({
        "platform": "wechat", "conversation_id": "a@chatroom", "message_id": "1",
        "md5": md5, "source_path": str(dat), "match_status": "verified_path",
      }) + "\n", "utf-8")

      media = work / "md" / "media"
      map_path = work / "image-map.json"
      mapping = pipeline.decode_images(index, media, map_path, KEY.decode(),
                                       pipeline.DEFAULT_TOOLS, 0xAC)
      self.assertIn(md5, mapping)
      self.assertTrue((media / f"{md5}.png").exists())
      self.assertEqual((media / f"{md5}.png").read_bytes(), PNG)

  def test_gif_keeps_extension(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      work = Path(tmp)
      dat = work / "demo.dat"
      dat.write_bytes(build_v2(GIF))
      md5 = fixtures.md5("gif")
      index = work / "media-index.jsonl"
      index.write_text(json.dumps({
        "platform": "wechat", "conversation_id": "a@chatroom", "message_id": "2",
        "md5": md5, "source_path": str(dat), "match_status": "verified_path",
      }) + "\n", "utf-8")
      media = work / "md" / "media"
      mapping = pipeline.decode_images(index, media, work / "map.json", KEY.decode(),
                                       pipeline.DEFAULT_TOOLS, 0xAC)
      self.assertIn(md5, mapping)
      self.assertTrue((media / f"{md5}.gif").exists(), sorted(p.name for p in media.iterdir()))


if __name__ == "__main__":
  unittest.main()
