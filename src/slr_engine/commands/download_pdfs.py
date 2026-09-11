"""
Download PDFs for full-text review queue.
- arXiv papers: fetched directly from arxiv.org
- DOI-only papers: tried via Unpaywall (open-access only)
Skips already-downloaded files.
"""

import csv
import time
import re
import sys
import ssl
import urllib.request
import urllib.error
import urllib.parse
import json
import os
from pathlib import Path

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# Reads the final reading list from 07_final/ and writes PDFs to 07_final/pdfs/.
from slr_engine.config import FINAL_DIR

OUT_DIR = FINAL_DIR

# Canonical final reading list (13_final_reading_list_<date>.csv); pick the newest.
_FINAL = sorted(OUT_DIR.glob("13_final_reading_list_*.csv"), reverse=True)

QUEUE = _FINAL[0] if _FINAL else (OUT_DIR / "13_final_reading_list.csv")
PDF_DIR = FINAL_DIR / "pdfs"
UNPAYWALL_EMAIL = "vanja.luk@gmail.com"

ARXIV_PDF = "https://arxiv.org/pdf/{arxiv_id}"
UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}?email={email}"


def safe_filename(paper_id: str, title: str) -> str:
    slug = re.sub(r"[^\w\-]", "_", title[:60]).strip("_")
    return f"{paper_id}__{slug}.pdf"


def download_url(url: str, dest: Path, label: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            content = resp.read()
        if len(content) < 5000 or not content.startswith(b"%PDF"):
            print(f"  [SKIP] {label} — not a valid PDF ({len(content)} bytes)")
            return False
        dest.write_bytes(content)
        print(f"  [OK]   {label} → {dest.name} ({len(content)//1024} KB)")
        return True
    except Exception as e:
        print(f"  [FAIL] {label} — {e}")
        return False


def try_unpaywall(doi: str, dest: Path, label: str) -> bool:
    url = UNPAYWALL_API.format(doi=urllib.parse.quote(doi, safe=""), email=UNPAYWALL_EMAIL)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        oa_url = data.get("best_oa_location") or {}
        pdf_url = oa_url.get("url_for_pdf") or oa_url.get("url")
        if not pdf_url:
            print(f"  [NO OA] {label} — no open-access PDF on Unpaywall")
            return False
        return download_url(pdf_url, dest, label)
    except Exception as e:
        print(f"  [FAIL] Unpaywall {label} — {e}")
        return False


def write_csv(rows: list[dict], fieldnames: list[str]) -> None:
    with open(QUEUE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    PDF_DIR.mkdir(exist_ok=True)

    with open(QUEUE) as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    if "downloaded" not in fieldnames:
        fieldnames.append("downloaded")
    for row in rows:
        row.setdefault("downloaded", "")

    # All papers in the final reading list should be fetched
    print(f"Queue: {len(rows)} papers to fetch\n")

    stats = {"arxiv_ok": 0, "unpaywall_ok": 0, "failed": 0, "skipped": 0}

    for i, row in enumerate(rows, 1):
        title = row["title"].strip()
        arxiv_id = row["arxiv_id"].strip() if row["arxiv_id"].strip() else ""
        doi = row["doi"].strip() if row["doi"].strip() else ""
        tier = row["tier"] or "?"

        # stable filename key: prefer arxiv_id, fall back to doi slug, then index
        if arxiv_id:
            file_key = arxiv_id.replace("/", "_")
        elif doi:
            file_key = re.sub(r"[^\w]", "_", doi)[:40]
        else:
            file_key = f"row{i:03d}"

        dest = PDF_DIR / safe_filename(file_key, title)

        print(f"[{i}/{len(rows)}] T{tier} | {title[:70]}")

        if dest.exists():
            print(f"  [CACHED] {dest.name}")
            stats["skipped"] += 1
            row["downloaded"] = "yes"
            continue

        ok = False

        if arxiv_id:
            url = ARXIV_PDF.format(arxiv_id=arxiv_id)
            ok = download_url(url, dest, f"arXiv:{arxiv_id}")
            if ok:
                stats["arxiv_ok"] += 1

        if not ok and doi:
            ok = try_unpaywall(doi, dest, f"DOI:{doi}")
            if ok:
                stats["unpaywall_ok"] += 1

        if ok:
            row["downloaded"] = "yes"

        if not ok:
            row["downloaded"] = "no"
            stats["failed"] += 1

        time.sleep(1.5)  # polite rate limit

    write_csv(rows, fieldnames)

    print(f"\nDone.")
    print(f"  arXiv fetched  : {stats['arxiv_ok']}")
    print(f"  Unpaywall OA   : {stats['unpaywall_ok']}")
    print(f"  Cached/skipped : {stats['skipped']}")
    print(f"  Not available  : {stats['failed']}")
    total_ok = stats["arxiv_ok"] + stats["unpaywall_ok"] + stats["skipped"]
    print(f"  PDFs in folder : {total_ok} / {len(rows)}")


if __name__ == "__main__":
    main()
