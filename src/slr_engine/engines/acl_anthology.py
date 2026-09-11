"""ACL Anthology engine — loads paper metadata from the local anthology data.

The acl-anthology Python package clones the anthology Git repo on first use
(~500 MB) into a platform-appropriate cache directory, or into the path set
by ACL_DATA_DIR.  Subsequent calls reuse the cached clone.

Citation graph data (references / forward citations) is NOT available in the
anthology XML.  Pair this engine with "ss" so Semantic Scholar provides the
citation links.

Paper ID formats accepted:
  10.18653/v1/2023.acl-long.1   — ACL Anthology DOI
  2023.acl-long.1               — bare anthology ID (new format)
  P19-1001                      — bare anthology ID (old format)
"""
from __future__ import annotations

from slr_engine.models import Paper

# Process-level singleton — loaded once on first fetch() call.
_anthology = None


def _get_anthology():
    global _anthology
    if _anthology is None:
        try:
            from acl_anthology import Anthology
        except ImportError as exc:
            raise ImportError(
                "acl-anthology is not installed.  Run: pip install acl-anthology"
            ) from exc

        from slr_engine.config import ACL_DATA_DIR

        print(
            "  [ACL] Initialising anthology"
            + (" (first run: cloning repo, ~500 MB) …" if not ACL_DATA_DIR else " …")
        )
        if ACL_DATA_DIR:
            from pathlib import Path
            _anthology = Anthology.from_repo(path=Path(ACL_DATA_DIR))
        else:
            _anthology = Anthology.from_repo()

    return _anthology


def _parse_acl_id(paper_id: str) -> str | None:
    """Return the bare anthology paper ID, or None if not an ACL paper."""
    pid = paper_id.strip()
    # ACL DOI → strip the prefix
    if pid.startswith("10.18653/v1/"):
        return pid[len("10.18653/v1/"):]
    # New-format anthology ID: YYYY.<venue>-<type>.<number>
    if pid and "." in pid and pid[0].isdigit():
        return pid
    # Old-format anthology ID: letter + 2-digit year + dash + number (e.g. P19-1001)
    if len(pid) >= 4 and pid[0].isalpha() and pid[1:3].isdigit() and "-" in pid:
        return pid
    return None


def fetch(paper_id: str) -> Paper | None:
    """
    Fetch paper metadata from the ACL Anthology local XML data.

    Returns a Paper with title, authors, year, venue, and DOI.
    references and citations are always empty — use the "ss" engine alongside
    this one to get the citation graph.
    """
    acl_id = _parse_acl_id(paper_id)
    if acl_id is None:
        return None

    try:
        anth  = _get_anthology()
        paper = anth.get_paper(acl_id)
        if paper is None:
            print(f"  [ACL 404] Not found: {acl_id}")
            return None
        return _to_model(paper)
    except KeyError:
        print(f"  [ACL 404] Not found: {acl_id}")
        return None
    except Exception as e:
        print(f"  [ACL ERROR] {acl_id}: {e}")
        return None


# ── Private helpers ───────────────────────────────────────────────────────────

def _to_model(paper) -> Paper:
    """Convert an acl_anthology.Paper to our Paper dataclass."""
    acl_id = paper.full_id
    doi    = paper.doi or f"10.18653/v1/{acl_id}"

    # Authors: NameSpecification objects → "First Last" strings
    authors: list[str] = []
    for ns in paper.authors or []:
        try:
            name = ns.canonical
            first = getattr(name, "first", "") or ""
            last  = getattr(name, "last",  "") or ""
            authors.append(f"{first} {last}".strip() if first else last)
        except Exception:
            authors.append(str(ns))

    # Venue: derive a short "VENUE YEAR" label from the volume ID.
    # Volume full_id examples: "2023.acl-long", "2019.emnlp-main", "P19"
    year  = str(paper.year or "")
    venue = _venue_label(paper.parent, year)

    title = str(paper.title or "")

    return Paper(
        paper_id      = acl_id,
        doi           = doi,
        title         = title,
        authors       = authors,
        year          = year,
        venue         = venue,
        citation_count= 0,  # not available in anthology XML
    )


def _venue_label(volume, year: str) -> str:
    """Return a short venue label such as 'ACL 2023' or 'EMNLP 2019'."""
    try:
        vol_id = volume.full_id  # e.g. "2023.acl-long" or "P19"
        parts  = vol_id.split(".")
        if len(parts) >= 2 and parts[0].isdigit():
            # New format: YYYY.venue-type → "VENUE YYYY"
            venue_code = parts[1].split("-")[0].upper()
            return f"{venue_code} {parts[0]}"
        # Old format: P19, D18, N18, Q18 …
        letter_map = {
            "P": "ACL", "D": "EMNLP", "N": "NAACL", "Q": "TACL",
            "E": "EACL", "S": "SemEval", "W": "Workshop",
        }
        if vol_id and vol_id[0].isalpha():
            code = letter_map.get(vol_id[0].upper(), vol_id[0].upper())
            yr   = ("20" + vol_id[1:3]) if len(vol_id) >= 3 else year
            return f"{code} {yr}"
    except Exception:
        pass
    # Fallback: use the volume title
    try:
        return str(volume.title or "")
    except Exception:
        return ""
