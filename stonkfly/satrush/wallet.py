"""Local keypair loading. The key never leaves this machine or enters logs."""

import json
import os
import stat
from pathlib import Path

from solders.keypair import Keypair


def load_keypair(path, env=os.environ):
    """A keypair file, or the SATRUSH_KEYPAIR_JSON environment variable (for
    hosted runs where the key is injected as a secret rather than a file)."""
    inline = env.get("SATRUSH_KEYPAIR_JSON")
    if inline:
        raw = json.loads(inline)
        if not isinstance(raw, list) or len(raw) != 64 or any(type(b) is not int for b in raw):
            raise ValueError("SATRUSH_KEYPAIR_JSON must be the 64-byte solana-keygen array")
        return Keypair.from_bytes(bytes(raw))
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError("Keypair file missing; see .env.example")
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name == "posix" and mode & 0o077:
        raise PermissionError("Keypair file must not be group/world readable (chmod 600)")
    raw = json.loads(path.read_text())
    if not isinstance(raw, list) or len(raw) != 64 or any(type(b) is not int for b in raw):
        raise ValueError("Keypair JSON must be the 64-byte solana-keygen array")
    return Keypair.from_bytes(bytes(raw))
