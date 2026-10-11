#!/usr/bin/env python3
"""Fast read-only live website link audit. Python standard library only."""

import csv
import re
import sys
import time
import argparse
import threading
from pathlib import Path
from queue import Queue
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WebsiteLinkAudit/1.0)"}
TIMEOUT = 12
WORKERS = 16
CACHE = {}
CACHE_LOCK = threading.Lock()


class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = None
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and self.base is None:
            self.base = a.get("href")
        if tag in ("a", "area") and a.get("href"):
            self.current = {"href": a["href"], "text": ""}
            self.links.append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag in ("a", "area"):
            self.current = None


def fetch(url):
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=TIMEOUT) as r:
            data = r.read()
            return r.status, r.geturl(), data, ""
    except HTTPError as e:
        return e.code, e.geturl(), b"", str(e)
    except Exception as e:
        return "ERROR", url, b"", str(e)


def check_url(url):
    url = urldefrag(url)[0]

    with CACHE_LOCK:
        if url in CACHE:
            return CACHE[url]

    status, final_url, _, error = fetch(url)
    result = classify(status)

    answer = {
        "status": status,
        "final_url": final_url,
        "result": result,
        "error": error,
    }

    with CACHE_LOCK:
        CACHE[url] = answer

    return answer


def classify(status):
    if status == 200:
        return "OK"
    if isinstance(status, int):
        if 300 <= status < 400:
            return "REDIRECT"
        if 400 <= status < 500:
            return "CLIENT_ERROR"
        if status >= 500:
            return "SERVER_ERROR"
    return "NETWORK_ERROR"


def get_sitemap(site):
    sitemap = urljoin(site, "sitemap.xml")
    status, _, body, error = fetch(sitemap)

    if not isinstance(status, int) or status >= 400:
        raise RuntimeError(
            f"Sitemap unavailable: {sitemap} ({status}: {error})"
        )

    urls = re.findall(
        rb"<loc>\s*(.*?)\s*</loc>", body, flags=re.I | re.S
    )
    return list(dict.fromkeys(
        x.decode("utf-8", "replace").strip() for x in urls
    ))


def inspect_page(page, site_host):
    status, final_url, body, error = fetch(page)

    page_row = {
        "page": page,
        "status": status,
        "final_url": final_url,
        "result": classify(status),
        "error": error,
    }

    if not isinstance(status, int) or status >= 400 or not body:
        return page_row, []

    parser = Parser()
    parser.feed(body.decode("utf-8", "replace"))

    base = urljoin(final_url, parser.base) if parser.base else final_url
    rows = []

    for link in parser.links:
        href = link["href"].strip()

        if not href or href.startswith(
            ("#", "javascript:", "mailto:", "tel:", "data:")
        ):
            continue

        target = urldefrag(urljoin(base, href))[0]
        parsed = urlparse(target)

        if parsed.scheme not in ("http", "https"):
            continue

        rows.append({
            "source_page": page,
            "link_text": " ".join(link["text"].split())[:200],
            "raw_href": href,
            "target_url": target,
            "same_site": parsed.netloc.lower() == site_host,
        })

    return page_row, rows


def write_csv(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser(
        description="Audit live website links and save CSV reports."
    )
    ap.add_argument(
        "url",
        nargs="?",
        default="https://dolatrammpi-coder.github.io/Quiz/",
        help="Website root URL",
    )
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    site = args.url.rstrip("/") + "/"
    site_host = urlparse(site).netloc.lower()
    output = Path("live_audit_reports")
    output.mkdir(parents=True, exist_ok=True)

    print("Website:", site)
    print("Reading sitemap...")
    pages = get_sitemap(site)

    # Keep pages belonging to the requested site.
    pages = [
        p for p in pages
        if urlparse(p).netloc.lower() == site_host
    ]

    if not pages:
        print("No site pages found in sitemap.")
        sys.exit(1)

    print(f"Pages found: {len(pages)}")
    print("Checking pages concurrently...")

    page_rows = []
    link_rows = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(inspect_page, page, site_host): page
            for page in pages
        }

        for i, future in enumerate(as_completed(futures), 1):
            try:
                page_row, links = future.result()
                page_rows.append(page_row)
                link_rows.extend(links)
            except Exception as e:
                page_rows.append({
                    "page": futures[future],
                    "status": "ERROR",
                    "final_url": "",
                    "result": "NETWORK_ERROR",
                    "error": str(e),
                })

            if i % 25 == 0 or i == len(futures):
                print(f"Pages scanned: {i}/{len(futures)}")

    print(f"Links discovered: {len(link_rows)}")
    print("Checking unique link targets...")

    unique_targets = list(dict.fromkeys(
        row["target_url"] for row in link_rows
    ))

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(check_url, target): target
            for target in unique_targets
        }

        for i, future in enumerate(as_completed(futures), 1):
            target = futures[future]
            try:
                results[target] = future.result()
            except Exception as e:
                results[target] = {
                    "status": "ERROR",
                    "final_url": target,
                    "result": "NETWORK_ERROR",
                    "error": str(e),
                }

            if i % 50 == 0 or i == len(futures):
                print(f"Targets checked: {i}/{len(futures)}")

    for row in link_rows:
        info = results[row["target_url"]]
        row.update(info)
        row["broken_candidate"] = info["result"] in (
            "CLIENT_ERROR", "SERVER_ERROR", "NETWORK_ERROR"
        )

    # A separate CSV for links that need attention.
    broken = [r for r in link_rows if r["broken_candidate"]]
    redirects = [r for r in link_rows if r["result"] == "REDIRECT"]

    write_csv(
        output / "page_status.csv",
        page_rows,
        ["page", "status", "final_url", "result", "error"],
    )
    write_csv(
        output / "all_links.csv",
        link_rows,
        [
            "source_page", "link_text", "raw_href", "target_url",
            "same_site", "status", "final_url", "result", "error",
            "broken_candidate",
        ],
    )
    write_csv(
        output / "broken_links.csv",
        broken,
        [
            "source_page", "link_text", "raw_href", "target_url",
            "status", "final_url", "result", "error",
        ],
    )
    write_csv(
        output / "redirects.csv",
        redirects,
        [
            "source_page", "link_text", "raw_href", "target_url",
            "status", "final_url", "result", "error",
        ],
    )

    counts = Counter(r["result"] for r in link_rows)
    page_counts = Counter(r["result"] for r in page_rows)

    summary = [
        f"Website: {site}",
        f"Pages in sitemap: {len(pages)}",
        f"Pages scanned: {len(page_rows)}",
        f"Links discovered: {len(link_rows)}",
        f"Unique URLs checked: {len(unique_targets)}",
        "",
        "LINK RESULTS:",
    ]
    summary.extend(f"{k}: {v}" for k, v in sorted(counts.items()))
    summary += ["", "PAGE RESULTS:"]
    summary.extend(f"{k}: {v}" for k, v in sorted(page_counts.items()))
    summary += [
        "",
        f"Broken candidates: {len(broken)}",
        f"Redirects: {len(redirects)}",
        "",
        "Important: 403, 429 and network errors may be caused by bot blocking.",
        "A 200 response confirms availability, not correct page content.",
        "Pages absent from the sitemap may not be included.",
    ]

    (output / "summary.txt").write_text(
        "\n".join(summary) + "\n", encoding="utf-8"
    )

    print("\n".join(summary))
    print("\nReports saved in:", output.resolve())


if __name__ == "__main__":
    main()
