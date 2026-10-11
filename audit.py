#!/usr/bin/env python3
# Live URL auditor for the Quiz GitHub Pages site.                               # Standard-library only; follows redirects and records final URL/status.
import csv                                                                       import re
import time                                                                      import ssl
import html                                                                      import urllib.request
import urllib.error                                                              import urllib.parse
import xml.etree.ElementTree as ET                                               from collections import deque
from html.parser import HTMLParser                                               from pathlib import Path
                                                                                 SITE = "https://dolatrammpi-coder.github.io/Quiz/"
HOST = "dolatrammpi-coder.github.io"                                             PREFIX = "/Quiz/"
OUT = Path.home() / "Quiz" / "live_url_audit.csv"                                TIMEOUT = 18
DELAY = 0.12                                                                     MAX_PAGES = 10000
UA = "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/131 Mobile Safari/537.36 QuizLiveURLAudit/1.0"
SKIP_SCHEMES = {"mailto", "tel", "javascript", "data", "blob", "sms", "intent"}  
class LinkParser(HTMLParser):                                                        def __init__(self):
        super().__init__(convert_charrefs=True)                                          self.base_href = None
        self.links = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)                                                                  if tag.lower() == "base" and a.get("href") and self.base_href is None:
            self.base_href = a["href"]
        for attr in ("href", "src", "action", "poster", "data-src"):
            val = a.get(attr)                                                                if val:
                self.links.append((tag.lower(), attr, val.strip()))
        srcset = a.get("srcset")
        if srcset:                                                                           for part in srcset.split(","):
                candidate = part.strip().split()
                if candidate:
                    self.links.append((tag.lower(), "srcset", candidate[0]))                                                                                      def clean_url(raw, base):                                                            raw = html.unescape((raw or "").strip())
    if not raw or raw.startswith("#"):                                                   return None                                                                  try:                                                                                 u = urllib.parse.urljoin(base, raw)                                              p = urllib.parse.urlsplit(u)                                                     if p.scheme.lower() in SKIP_SCHEMES or p.scheme.lower() not in ("http", "https"):                                                                                     return None                                                                  # Remove fragments; retain query because it can change the response.             return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))                                                              except Exception:                                                                    return None                                                                                                                                               def in_scope(url):
    p = urllib.parse.urlsplit(url)                                                   return p.hostname == HOST and (p.path == PREFIX.rstrip("/") or p.path.startswith(PREFIX))                                                                                                                                                      def fetch(url):                                                                      req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})                                                                                    try:                                                                                 with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as r:                                                                         data = r.read(2_500_000)                                                         ctype = r.headers.get("Content-Type", "")                                        return r.status, r.geturl(), ctype, data, ""
    except urllib.error.HTTPError as e:                                                  try:
            data = e.read(2000)                                                          except Exception:
            data = b""                                                                   return e.code, e.geturl() or url, e.headers.get("Content-Type", "") if e.headers else "", data, str(e)
    except Exception as e:
        return "", url, "", b"", f"{type(e).__name__}: {e}"                      
def status_class(code, err):                                                         if err:
        if "timed out" in err.lower() or "timeout" in err.lower():                           return "TIMEOUT_OR_NETWORK"
        return "NETWORK_ERROR"                                                       try:
        n = int(code)                                                                except Exception:                                                                    return "UNKNOWN"
    if 200 <= n < 300:                                                                   return "OK"                                                                  if 300 <= n < 400:                                                                   return "REDIRECT_OR_REDIRECT_TARGET"
    if n == 404 or n == 410:                                                             return "BROKEN_CONFIRMED"                                                    if n in (401, 403, 429):                                                             return "MANUAL_CHECK_BLOCKED"
    if n >= 500:                                                                         return "MANUAL_CHECK_SERVER_ERROR"                                           if n >= 400:                                                                         return "HTTP_ERROR"
    return "UNKNOWN"                                                                                                                                              def main():                                                                          print("Quiz live URL audit starting...")
    print("Site:", SITE)                                                             sitemap_urls = [                                                                     urllib.parse.urljoin(SITE, "sitemap.xml"),                                       urllib.parse.urljoin(SITE, "sitemap_index.xml"),
    ]                                                                                queue = deque()                                                                  queued = set()                                                                   checked = {}
    rows = []                                                                        page_sources = {}                                                                def enqueue(url, source, kind):                                                      if not url or not in_scope(url):
            return                                                                       if url not in queued:                                                                queued.add(url)                                                                  queue.append((url, source, kind))
    for u in sitemap_urls:                                                               enqueue(u, "START", "sitemap")                                               enqueue(urllib.parse.urljoin(SITE, "robots.txt"), "START", "robots")             enqueue(urllib.parse.urljoin(SITE, "index.html"), "START", "page")
    link_seen = set()                                                                                                                                                 while queue and len(checked) < MAX_PAGES:                                            url, source, kind = queue.popleft()
        if url in checked:                                                                   continue                                                                     code, final_url, ctype, body, err = fetch(url)                                   cls = status_class(code, err)
        checked[url] = (code, final_url, ctype, err, cls)                                print(f"[{len(checked)}] {code or 'ERR'} {cls} {url}")                           rows.append({                                                                        "requested_url": url, "final_url": final_url, "http_status": code,
            "result": cls, "content_type": ctype, "discovered_from": source,                 "link_type": kind, "error": err, "redirected": "YES" if final_url != url else "NO"                                                                            })
        time.sleep(DELAY)                                                                if err or not isinstance(code, int) or not (200 <= code < 300):                      continue                                                                     p = urllib.parse.urlsplit(final_url)
        path_lower = p.path.lower()                                                      if "xml" in ctype.lower() or path_lower.endswith(".xml"):                            try:                                                                                 root = ET.fromstring(body)
                for loc in root.iter():                                                              if loc.tag.split("}")[-1].lower() == "loc" and loc.text:                             target = clean_url(loc.text, final_url)                                          if target and in_scope(target):
                            enqueue(target, final_url, "sitemap_url")                        except Exception:                                                                    pass                                                                         continue
        if path_lower.endswith("/robots.txt") or path_lower.endswith("robots.txt"):                                                                                           text_body = body.decode("utf-8", "replace")                                      for sm in re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", text_body):
                target = clean_url(sm, final_url)                                                if target and in_scope(target):                                                      enqueue(target, final_url, "sitemap")                                    continue
        if "html" not in ctype.lower() and not path_lower.endswith((".html", ".htm", "/")):                                                                                   continue                                                                     parser = LinkParser()
        try:                                                                                 parser.feed(body.decode("utf-8", "replace"))                                 except Exception:                                                                    continue
        base = clean_url(parser.base_href, final_url) if parser.base_href else final_url                                                                                  if not base:                                                                         base = final_url
        for tag, attr, raw in parser.links:
            target = clean_url(raw, base)                                                    if not target or not in_scope(target):
                continue                                                                     key = (target, final_url, tag, attr, raw)
            if key not in link_seen:                                                             link_seen.add(key)
                page_sources.setdefault(target, final_url)                                       enqueue(target, final_url, f"{tag}.{attr}")
    # Report unique requested URLs with source and actual HTTP resolution.           OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["requested_url", "final_url", "http_status", "result", "content_type", "discovered_from", "link_type", "error", "redirected"]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:                           w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()                                                                  w.writerows(rows)
    counts = {}                                                                      for r in rows:
        counts[r["result"]] = counts.get(r["result"], 0) + 1                         print("\n========== AUDIT SUMMARY ==========")
    print("Unique live URLs checked:", len(rows))                                    for k in sorted(counts):
        print(f"{k}: {counts[k]}")                                                   print("CSV report:", OUT)
    print("Note: 403/429, timeouts, network errors and 5xx are NOT automatically called broken; manually verify them.")
    print("This audit checks reachable same-site URLs under /Quiz/ and follows actual HTTP redirects.")
                                                                                 if __name__ == "__main__":
