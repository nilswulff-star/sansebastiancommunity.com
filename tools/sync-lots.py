# -*- coding: utf-8 -*-
"""Extract the San Sebastian plat geometry + lot data from the server-rendered
page of the existing widget, into a clean JSON file we own."""
"""Re-sync assets/lots.json from the developer's live plat app.

    python tools/sync-lots.py

Pulls the server-rendered page, extracts every lot polygon plus its status,
phase, price, size and address, and rewrites assets/lots.json in place.
Run it whenever availability changes.
"""
import json
import os
import re
import html
import urllib.request

SOURCE = 'https://subdivision-plat-app.vercel.app/c/san-sebastian-mpc'
req = urllib.request.Request(SOURCE, headers={'User-Agent': 'Mozilla/5.0'})
raw = urllib.request.urlopen(req, timeout=60).read().decode('utf-8', 'replace')

# The page is a Next.js RSC payload; the SVG markup is embedded with escaped quotes.
txt = raw.replace('\\"', '"').replace('\\n', '\n').replace('\\/', '/')

vb = re.search(r'viewBox="([\d.\s-]+)"', txt)
viewBox = vb.group(1).strip() if vb else None

poly_re = re.compile(
    r'<polygon\b[^>]*?class="(public-lot[^"]*)"[^>]*?points="([^"]+)"[^>]*?aria-label="([^"]*)"[^>]*>',
    re.S)
alt_re = re.compile(
    r'<polygon\b[^>]*?points="([^"]+)"[^>]*?class="(public-lot[^"]*)"[^>]*?aria-label="([^"]*)"[^>]*>',
    re.S)

found = {}
for cls, pts, aria in poly_re.findall(txt):
    found[aria] = (cls, pts)
for pts, cls, aria in alt_re.findall(txt):
    found.setdefault(aria, (cls, pts))

# Fallback: attributes in any order
if len(found) < 100:
    for tag in re.findall(r'<polygon\b[^>]*>', txt):
        if 'public-lot' not in tag:
            continue
        c = re.search(r'class="([^"]+)"', tag)
        p = re.search(r'points="([^"]+)"', tag)
        a = re.search(r'aria-label="([^"]*)"', tag)
        if c and p and a:
            found.setdefault(a.group(1), (c.group(1), p.group(1)))

# Text labels (lot numbers) with their placement
labels = []
for m in re.finditer(r'<text\b([^>]*)>([^<]*)</text>', txt):
    attrs, body = m.group(1), html.unescape(m.group(2)).strip()
    if not body:
        continue
    gx = re.search(r'\bx="([-\d.]+)"', attrs)
    gy = re.search(r'\by="([-\d.]+)"', attrs)
    if gx and gy:
        labels.append({'t': body, 'x': float(gx.group(1)), 'y': float(gy.group(1))})

seen_lbl = {}
for l in labels:
    seen_lbl.setdefault(l['t'], l)
labels = list(seen_lbl.values())


def parse(aria):
    parts = [p.strip() for p in html.unescape(aria).split('·')]
    o = {'label': parts[0]}
    for f in parts[2:]:
        if re.match(r'^Phase', f, re.I):
            o['phase'] = re.sub(r'^Phase\s*', '', f, flags=re.I)
        elif f.startswith('$'):
            o['price'] = int(re.sub(r'[^0-9]', '', f))
        elif f.endswith('sq ft'):
            o['sqft'] = float(re.sub(r'[^0-9.]', '', f))
        elif f.endswith('acres'):
            o['acres'] = float(re.sub(r'[^0-9.]', '', f))
        else:
            o['address'] = f
    return o


lots = []
for aria, (cls, pts) in found.items():
    o = parse(aria)
    o['status'] = cls.replace('public-lot', '').replace('status-', '').strip()
    o['points'] = ' '.join(pts.split())
    lots.append(o)


def keyfn(l):
    m = re.search(r'(\d+)', l['label'])
    return (0 if l['label'].upper().startswith('LOT ') and 'sqft' not in l else 1,
            int(m.group(1)) if m else 0)


lots.sort(key=lambda l: int(re.search(r'(\d+)', l['label']).group(1)) if re.search(r'(\d+)', l['label']) else 0)

# Classify: commercial pads come through as "Lot LOT 1".."Lot LOT 8"
ACRES = {1: 1.60, 2: 1.03, 3: 1.24, 4: 1.24, 5: 1.24, 6: 1.24, 7: 1.37, 8: 1.01}
# Pads 1-4 sell as Phase 1 Lots 1-4; pads 5-8 sell as Phase 2 Lots 1-4.
# platRef keeps the recorded plat number so the map ties back to the document.
for l in lots:
    m = re.match(r'^Lot LOT (\d+)$', l['label'])
    if m:
        plat_n = int(m.group(1))
        l['kind'] = 'commercial'
        l['platRef'] = plat_n
        l['phase'] = '1' if plat_n <= 4 else '2'
        l['n'] = plat_n if plat_n <= 4 else plat_n - 4
        l['label'] = 'Lot %d' % l['n']
        if plat_n in ACRES:
            l['acres'] = ACRES[plat_n]
            l['sqft'] = round(ACRES[plat_n] * 43560)
    else:
        l['kind'] = 'residential'
        d = re.search(r'(\d+)', l['label'])
        l['n'] = int(d.group(1)) if d else 0
lots.sort(key=lambda l: (l['kind'] != 'residential', l.get('platRef', l['n'])))

# Apply manual corrections from assets/lot-overrides.json. These win over the
# source app, so a re-sync never undoes a correction made here.
OVERRIDES = 'assets/lot-overrides.json'
applied = 0
if os.path.exists(OVERRIDES):
    ov = json.load(open(OVERRIDES, encoding='utf-8')).get('lots', {})
    for l in lots:
        key = ('Commercial P%s-%s' % (l.get('phase'), l['n'])) if l['kind'] == 'commercial' else l['label']
        if key in ov:
            l.update(ov[key])
            applied += 1
    unknown = set(ov) - {(('Commercial P%s-%s' % (x.get('phase'), x['n'])) if x['kind'] == 'commercial' else x['label']) for x in lots}
    if unknown:
        print('WARNING: overrides not matched to any lot:', ', '.join(sorted(unknown)))

# A sold lot never publishes a price.
for l in lots:
    if l.get('status') == 'sold':
        l.pop('price', None)

out = {'viewBox': viewBox, 'lots': lots, 'labels': labels}
dest = 'assets/lots.json'
os.makedirs('assets', exist_ok=True)
json.dump(out, open(dest, 'w', encoding='utf-8'), separators=(',', ':'))

counts = {}
for l in lots:
    counts[l['status']] = counts.get(l['status'], 0) + 1
priced = [l for l in lots if 'price' in l]
avail = [l for l in lots if l['status'] == 'available' and 'price' in l]
print('viewBox       :', viewBox)
print('lots extracted:', len(lots))
print('labels        :', len(labels))
print('by status     :', counts)
print('with geometry :', sum(1 for l in lots if l.get('points')))
print('with price    :', len(priced))
print('available $   :', (min(l["price"] for l in avail), max(l["price"] for l in avail)) if avail else 'n/a')
print('with address  :', sum(1 for l in lots if l.get('address')))
print('file          :', dest, os.path.getsize(dest), 'bytes')
print('overrides     :', applied)
print('sample        :', json.dumps({k: v for k, v in lots[0].items() if k != 'points'}))
