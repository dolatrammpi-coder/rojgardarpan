# Publishing workflow

1. GitHub Action रोज official source pages check करता है।
2. `data/source-status.json` में status/hash आता है।
3. Source बदलने पर official notification manually verify करें।
4. Verified job को `data/jobs.json` में जोड़ें।
5. Commit/push के बाद GitHub Pages पर job दिखाई देगी।

यह checker दूसरे job portals से articles scrape करके publish नहीं करता। अलग-अलग recruitment sites का HTML अलग होता है, इसलिए शुरुआत में universal auto-extraction नहीं रखा गया है।
