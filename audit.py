#!/usr/bin/env python3
"""Live canonical and SEO audit. Read-only; standard library only."""

import csv
import re
import sys
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

DEFAULT_SITE = "https://dolatrammpi-coder.github.io/Quiz/"
TIMEOUT = 15
WORKERS = 16
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LiveSEOAudit/1.0)"}


class SEOParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonicals = []
        self.descriptions = []
        self.robots = []
        self.h1_count = 0
        self.title = ""
        self.in_title = False
        self.in_h1 = False
        self.h1_text = []
        self.title_text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        tag = tag.lower()

        if tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonicals.append(a.get("href", "").strip())

        if tag == "meta":
            name = a.get("name", "").lower().strip()
            if name == "description":
                self.descriptions.append(a.get("content", "").strip())
            elif name == "robots":
                self.robots.append(a.get("content", "").strip())

        if tag == "title":
            self.in_title = True

        if tag == "h1":
            self.h1_count += 1
            self.in_h1 = True
            self.h1_text.append("")

    def handle_data(self, data):
        if self.in_title:
            self.title_text.append(data)
        if self.in_h1:
            self.h1_text[-1] += data

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        elif tag == "h1":
            self.in_h1 = False


def fetch(url):
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=TIMEOUT) as response:
            return response.status, response.geturl(), response.read(), ""
    except HTTPError as exc:
        return exc.code, exc.geturl(), b"", str(exc)
    except Exception as exc:
        return "ERROR", url, b"", str(exc)


def get_sitemap_urls(site):
    sitemap = urljoin(site, "sitemap.xml")
    status, _, body, error = fetch(sitemap)

    if not isinstance(status, int) or status >= 400:
        raise RuntimeError(
            f"Cannot read sitemap: {sitemap}; status={status}; {error}"
        )

    urls = re.findall(
        rb"<loc>\s*(.*?)\s*</loc>", body, flags=re.I | re.S
    )
    return list(dict.fromkeys(
        u.decode("utf-8", "replace").strip() for u in urls
    ))


def audit_page(url, site_host):
    status, final_url, body, error = fetch(url)

    row = {
        "page_url": url,
        "http_status": status,
        "final_url": final_url,
        "title": "",
        "h1_count": "",
        "canonical_count": "",
        "canonical_url": "",
        "canonical_status": "",
        "description_count": "",
        "description_length": "",
        "robots_meta": "",
        "issues": "",
        "error": error,
    }

    if not isinstance(status, int) or status >= 400 or not body:
        row["issues"] = "PAGE_UNAVAILABLE"
        return row

    parser = SEOParser()
    parser.feed(body.decode("utf-8", "replace"))

    title = " ".join("".join(parser.title_text).split())
    canonicals = parser.canonicals
    descriptions = parser.descriptions
    issues = []

    row["title"] = title
    row["h1_count"] = parser.h1_count
    row["canonical_count"] = len(canonicals)
    row["description_count"] = len(descriptions)
    row["robots_meta"] = " | ".join(parser.robots)

    if title == "":
        issues.append("MISSING_TITLE")

    if parser.h1_count == 0:
        issues.append("MISSING_H1")
    elif parser.h1_count > 1:
        issues.append("MULTIPLE_H1")

    if not descriptions or not descriptions[0]:
        issues.append("MISSING_DESCRIPTION")
        row["description_length"] = 0
    else:
        row["description_length"] = len(descriptions[0])

    if len(canonicals) == 0:
        issues.append("MISSING_CANONICAL")
        row["canonical_status"] = "MISSING"
    elif len(canonicals) > 1:
        issues.append("MULTIPLE_CANONICALS")
        row["canonical_status"] = "MULTIPLE"
        row["canonical_url"] = " | ".join(canonicals)
    else:
        canonical = canonicals[0]
        row["canonical_url"] = canonical

        if not canonical:
            issues.append("EMPTY_CANONICAL")
            row["canonical_status"] = "EMPTY"
        else:
            resolved = urljoin(final_url, canonical)
            row["canonical_url"] = resolved
            parsed = urlparse(resolved)

            if parsed.scheme not in ("http", "https"):
                issues.append("INVALID_CANONICAL_URL")
                row["canonical_status"] = "INVALID_URL"
            elif parsed.netloc.lower() != site_host:
                issues.append("EXTERNAL_CANONICAL")
                row["canonical_status"] = "EXTERNAL"
            elif urldefrag(resolved)[0].rstrip("/") != urldefrag(final_url)[0].rstrip("/"):
                issues.append("CANONICAL_DIFFERS_FROM_PAGE")
                row["canonical_status"] = "DIFFERENT_URL"
            else:
                row["canonical_status"] = "SELF_CANONICAL"

    robots = ",".join(parser.robots).lower()
    if "noindex" in robots:
        issues.append("ROBOTS_NOINDEX")

    row["issues"] = ";".join(issues) if issues else "NONE"
    return row


def save_csv(path, rows):
    columns = [
        "page_url", "http_status", "final_url", "title", "h1_count",
        "canonical_count", "canonical_url", "canonical_status",
        "description_count", "description_length", "robots_meta",
        "issues", "error",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    site = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SITE
    site = site.rstrip("/") + "/"
    site_host = urlparse(site).netloc.lower()

    output = Path("live_seo_reports")
    output.mkdir(parents=True, exist_ok=True)

    print("LIVE SEO AUDIT:", site)
    print("Fetching sitemap...")

    try:
        urls = get_sitemap_urls(site)
    except Exception as exc:
        print("ERROR:", exc)
        sys.exit(1)

    urls = [
        u for u in urls
        if urlparse(u).netloc.lower() == site_host
    ]

    print("Sitemap pages:", len(urls))
    rows = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(audit_page, u, site_host): u for u in urls
        }

        for i, future in enumerate(as_completed(futures), 1):
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append({
                    "page_url": futures[future],
                    "http_status": "ERROR",
                    "final_url": "",
                    "issues": "AUDIT_ERROR",
                    "error": str(exc),
                })

            if i % 50 == 0 or i == len(futures):
                print(f"Pages checked: {i}/{len(futures)}")

    rows.sort(key=lambda r: r.get("page_url", ""))
    save_csv(output / "page_seo_audit.csv", rows)

    issue_counts = Counter()
    for row in rows:
        for issue in row.get("issues", "").split(";"):
            if issue and issue != "NONE":
                issue_counts[issue] += 1

    summary = [
        f"Website: {site}",
        f"Sitemap pages: {len(urls)}",
        f"Pages audited: {len(rows)}",
        "",
        "ISSUE COUNTS (pages may have multiple issues):",
    ]
    summary.extend(
        f"{name}: {count}" for name, count in sorted(issue_counts.items())
    )
    summary += [
        "",
        "This is a live HTML audit, not proof of Google indexing.",
        "Canonical differences are flags for review, not automatic errors.",
        "Check Google Search Console for actual indexing status.",
    ]

    (output / "summary.txt").write_text(
        "\n".join(summary) + "\n", encoding="utf-8"
    )

    print("\n".join(summary))
    print("\nReports saved to:", output.resolve())


if __name__ == "__main__":
    main()
