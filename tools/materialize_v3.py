from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = ROOT / "v3_payload"
encoded = "".join(path.read_text(encoding="utf-8").strip() for path in sorted(PARTS.glob("part*.txt"), key=lambda p: int(p.stem[4:])))
archive = base64.b85decode(encoded.encode("ascii"))
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
    members = tar.getmembers()
    for member in members:
        target = (ROOT / member.name).resolve()
        if ROOT.resolve() not in target.parents and target != ROOT.resolve():
            raise RuntimeError(f"Unsafe archive path: {member.name}")
    tar.extractall(ROOT)
print(f"Materialized {len(members)} V3 archive entries")
