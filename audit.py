#!/usr/bin/env python3
import csv
import time
import html
import ssl
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from collections import deque
from html.parser import HTMLParser
from pathlib import Path

SITE = "https://dolatrammpi-coder.github.io/Quiz/"
HOST = "dolatrammpi-coder.github.io"
PREFIX = "/Quiz/"
OUT = Path.home() / "Quiz" / "live_url_audit.csv"
TIMEOUT = 18
DELAY = 0.12
MAX_PAGES = 10000

class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]

        for attr in ("href", "src", "action", "poster", "data-src"):
            if a.get(attr):
                self.links.append((tag, attr, a[attr]))

        if a.get("srcset"):
            for item in a["srcset"].split(","):
                parts = item.strip().split()
                if parts:
                    self.links.append((tag, "srcset", parts[0]))


def normalize(raw, base):
    raw = html.unescape((raw or "").strip())
    if not raw or raw.startswith("#"):
        return None

    try:
        url = urllib.parse.urljoin(base, raw)
        p = urllib.parse.urlsplit(url)

        if p.scheme.lower() not in ("http", "https"):
            return None

        return urllib.parse.urlunsplit((
            p.scheme.lower(),
            p.netloc.lower(),
            p.path or "/",
            p.query,
            ""
        ))
    except Exception:
        return None


def in_scope(url):
    p = urllib.parse.urlsplit(url)
    return (
        p.hostname == HOST
        and (
            p.path == PREFIX.rstrip("/")
            or p.path.startswith(PREFIX)
        )
    )


def fetch(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 QuizLiveURLAudit/1.0"
        }
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=TIMEOUT,
            context=ssl.create_default_context()
        ) as response:
            body = response.read(2500000)
            return (
                response.status,
                response.geturl(),
                response.headers.get("Content-Type", ""),
                body,
                ""
            )

    except urllib.error.HTTPError as e:
        return (
            e.code,
            e.geturl() or url,
            e.headers.get("Content-Type", "") if e.headers else "",
            b"",
            ""
        )

    except Exception as e:
        return "", url, "", b"", str(e)


def classify(status, error):
    if error:
        if "timeout" in error.lower():
            return "TIMEOUT_OR_NETWORK"
        return "NETWORK_ERROR"

    try:
        code = int(status)
    except Exception:
        return "UNKNOWN"

    if 200 <= code < 300:
        return "OK"
    if code in (404, 410):
        return "BROKEN_CONFIRMED"
    if code in (401, 403, 429):
        return "MANUAL_CHECK_BLOCKED"
    if code >= 500:
        return "MANUAL_CHECK_SERVER_ERROR"
    if code >= 400:
        return "HTTP_ERROR"
    return "OTHER"


def main():
    queue = deque()
    queued = set()
    rows = []

    def add(url, source, kind):
        if url and in_scope(url) and url not in queued:
            queued.add(url)
            queue.append((url, source, kind))

    add(SITE + "sitemap.xml", "START", "sitemap")
    add(SITE + "sitemap_index.xml", "START", "sitemap")
    add(SITE + "robots.txt", "START", "robots")
    add(SITE + "index.html", "START", "page")

    while queue and len(rows) < MAX_PAGES:
        url, source, kind = queue.popleft()

        status, final_url, ctype, body, error = fetch(url)
        result = classify(status, error)

        print(f"[{len(rows)+1}] {status or 'ERR'} {result} {url}")

        rows.append({
            "requested_url": url,
            "final_url": final_url,
            "http_status": status,
            "result": result,
            "content_type": ctype,
            "discovered_from": source,
            "link_type": kind,
            "error": error,
            "redirected": "YES" if final_url != url else "NO"
        })

        time.sleep(DELAY)

        if error or not isinstance(status, int):
            continue

        if not 200 <= status < 300:
            continue

        path = urllib.parse.urlsplit(final_url).path.lower()

        if "xml" in ctype.lower() or path.endswith(".xml"):
            try:
                root = ET.fromstring(body)
                for node in root.iter():
                    if node.tag.split("}")[-1].lower() == "loc":
                        target = normalize(node.text, final_url)
                        add(target, final_url, "sitemap_url")
            except Exception:
                pass
            continue

        if path.endswith("robots.txt"):
            text = body.decode("utf-8", "replace")
            for line in text.splitlines():
                if line.lower().startswith("sitemap:"):
                    target = normalize(line.split(":", 1)[1], final_url)
                    add(target, final_url, "sitemap")
            continue

        if "html" not in ctype.lower() and not path.endswith(
            (".html", ".htm", "/")
        ):
            continue

        parser = Parser()
        try:
            parser.feed(body.decode("utf-8", "replace"))
        except Exception:
            continue

        base = normalize(parser.base, final_url) if parser.base else final_url
        if not base:
            base = final_url

        for tag, attr, raw in parser.links:
            target = normalize(raw, base)
            add(target, final_url, f"{tag}.{attr}")

    OUT.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "requested_url",
        "final_url",
        "http_status",
        "result",
        "content_type",
        "discovered_from",
        "link_type",
        "error",
        "redirected"
    ]

    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print("\n========== AUDIT SUMMARY ==========")
    print("URLs checked:", len(rows))

    counts = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1

    for result, count in sorted(counts.items()):
        print(f"{result}: {count}")

    print("CSV report:", OUT)
    print("Audit complete.")


if __name__ == "__main__":
    main()
