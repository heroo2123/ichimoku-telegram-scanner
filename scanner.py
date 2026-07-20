"""Runtime loader for the full Ichimoku scanner implementation.

The implementation is stored as a compressed, ASCII-safe payload split across
small repository files so GitHub's contents API can publish it reliably.
"""
from __future__ import annotations

import base64
import zlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_parts = []
for _index in range(5):
    _parts.append((_ROOT / "scanner_payload" / f"part{_index}.txt").read_text(encoding="utf-8").strip())
_source = zlib.decompress(base64.b85decode("".join(_parts).encode("ascii"))).decode("utf-8")
exec(compile(_source, str(_ROOT / "scanner_impl.py"), "exec"), globals(), globals())
