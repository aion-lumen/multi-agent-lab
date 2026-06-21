"""MockIMAPSession — read JSON fixtures, mimic life-mail IMAPSession.

Design per state/prompt-o1-design-notes.md §G3-A.2. Never touches the network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class MockMailEnvelope:
    uid: int
    flags: list[str] = field(default_factory=list)
    message_id: str = ""
    date: str = ""
    from_name: str = ""
    from_addr: str = ""
    to_addr: str = ""
    subject: str = ""
    body_text: str = ""
    body_truncated: bool = False
    has_attachment: bool = False
    list_id: str = ""
    raw_size: int = 0

    def is_already_classified(self) -> bool:
        return False


class MockIMAPSession:
    """Stand-in for life-mail's IMAPSession. Reads envelopes from a JSON fixture."""

    UIDVALIDITY = 1_000_000

    def __init__(self, fixture_path: Path, **_):
        self._envelopes = [
            MockMailEnvelope(**e) for e in json.loads(Path(fixture_path).read_text())
        ]

    def __enter__(self) -> "MockIMAPSession":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def select_folder(self, folder: str = "INBOX") -> tuple[int, int]:
        return len(self._envelopes), self.UIDVALIDITY

    def search_uids(
        self,
        since_uid: int = 0,
        skip_classified: bool = False,
        limit: int | None = None,
        date_since: str | None = None,
        date_before: str | None = None,
    ) -> list[int]:
        uids = sorted(e.uid for e in self._envelopes)
        if limit is not None:
            uids = uids[:limit]
        return uids

    def fetch_envelopes(self, uids: list[int]) -> Iterator[MockMailEnvelope]:
        keep = set(uids)
        for e in self._envelopes:
            if e.uid in keep:
                yield e

    def add_keywords(self, *_args, **_kwargs) -> bool:
        return False  # Yahoo behaviour parity

    def remove_keywords(self, *_args, **_kwargs) -> bool:
        return False

    def get_flags(self, _uid: int) -> list[str]:
        return []
