"""Domain model — no external imports."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Paper:
    paper_id: str = ""
    eid: str = ""
    doi: str = ""
    arxiv_id: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    venue: str = ""
    citation_count: int = 0
    references: list["Paper"] = field(default_factory=list)
    citations: list["Paper"] = field(default_factory=list)
    source_engine: str = ""

    @property
    def dedup_key(self) -> str:
        if self.doi:
            d = self.doi.strip().lower()
            return d[4:] if d.startswith("doi:") else d
        return self.paper_id
