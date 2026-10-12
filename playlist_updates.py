"""Apply explicitly reviewed playlist changes without matching other feeds."""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


def channel_identity(section: str, block: list[str]) -> tuple[str, str, str]:
    extinf = block[0]
    name = extinf.rsplit(",", 1)[-1].strip().lower()
    guide = re.search(r'\btvg-id="([^"]*)"', extinf)
    return section, name, guide.group(1) if guide else ""


def rule_identity(rule: dict) -> tuple[str, str, str]:
    return rule["section"], rule["name"].strip().lower(), rule["tvg_id"]


def require_stream_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Replacement is not an HTTP(S) stream URL")
    if parsed.username or parsed.password:
        raise ValueError("Replacement must not contain login credentials")
    return value


class ReviewedUpdates:
    def __init__(self, path: Path, resolve=None):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.removals = {rule_identity(rule) for rule in data["remove"]}
        self.replacements = {rule_identity(rule): rule for rule in data["replace"]}
        if len(self.removals) != len(data["remove"]):
            raise ValueError("Duplicate removal rule")
        if len(self.replacements) != len(data["replace"]):
            raise ValueError("Duplicate replacement rule")
        if self.removals & self.replacements.keys():
            raise ValueError("A channel cannot be both removed and replaced")
        for rule in self.replacements.values():
            require_stream_url(rule["url"])
        self.resolve = resolve

    def apply(self, section: str, block: list[str]) -> list[str] | None:
        identity = channel_identity(section, block)
        if identity in self.removals:
            return None
        rule = self.replacements.get(identity)
        if rule is None:
            return block

        url = rule["url"]
        if rule.get("refresh"):
            try:
                if self.resolve is None:
                    raise ValueError("No provider resolver configured")
                url = require_stream_url(self.resolve(rule, block[-1]))
            except Exception as exc:
                # Avoid leaking provider responses, tokens or query strings in CI.
                print(
                    f"Could not refresh {rule['section']}: {rule['name']} "
                    f"({type(exc).__name__}); using reviewed fallback URL",
                    file=sys.stderr,
                )

        # Retain channel identity/EPG metadata. Remove old stream-specific player
        # headers and DASH/DRM settings; the replacements are clear HLS streams.
        options = [
            line for line in block[1:-1]
            if not line.startswith(("#EXTVLCOPT:", "#KODIPROP:", "#EXTHTTP:"))
        ]
        return [block[0], *options, *rule.get("options", []), url]
