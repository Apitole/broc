#!/usr/bin/env python3
"""
Scraper multi-sources — Toute la France, 14 jours glissants.
Sources:
  1. brocabrac.fr       — JSON-LD par departement
  2. vide-greniers.org  — JSON-LD par departement
  3. sabradou.com       — HTML par date (Nord/Picardie) + geocodage

Concu pour tourner en cron GitHub Actions (1x/jour).
Genere public/events.json consomme par le frontend statique.
"""

import json
import math
import os
import re
import sys
import time
from datetime import datetime, date, timedelta

import requests
from bs4 import BeautifulSoup

# ── CONFIG ───────────────────────────────────────────────────────
DAYS_AHEAD = 14
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "public", "events.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Tous les departements metropolitains (pour brocabrac)
DEPARTMENTS_NUM = [
    f"{i:02d}" for i in range(1, 20)
] + ["2A", "2B"] + [
    f"{i}" for i in range(21, 96)
]

# Departements pour vide-greniers.org (nom tel qu'utilise dans l'URL)
VG_DEPARTMENTS = [
    "Ain", "Aisne", "Allier", "Alpes-de-Haute-Provence", "Hautes-Alpes",
    "Alpes-Maritimes", "Ardeche", "Ardennes", "Ariege", "Aube", "Aude",
    "Aveyron", "Bouches-du-Rhone", "Calvados", "Cantal", "Charente",
    "Charente-Maritime", "Cher", "Correze", "Cote-d-or", "Cotes-d-Armor",
    "Creuse", "Dordogne", "Doubs", "Drome", "Eure", "Eure-et-Loir",
    "Finistere", "Corse-du-Sud", "Haute-Corse", "Gard", "Haute-Garonne",
    "Gers", "Gironde", "Herault", "Ille-et-Vilaine", "Indre",
    "Indre-et-Loire", "Isere", "Jura", "Landes", "Loir-et-Cher", "Loire",
    "Haute-Loire", "Loire-Atlantique", "Loiret", "Lot", "Lot-et-Garonne",
    "Lozere", "Maine-et-Loire", "Manche", "Marne", "Haute-Marne", "Mayenne",
    "Meurthe-et-Moselle", "Meuse", "Morbihan", "Moselle", "Nievre", "Nord",
    "Oise", "Orne", "Pas-de-Calais", "Puy-de-Dome", "Pyrenees-Atlantiques",
    "Hautes-Pyrenees", "Pyrenees-Orientales", "Bas-Rhin", "Haut-Rhin",
    "Rhone", "Haute-Saone", "Saone-et-Loire", "Sarthe", "Savoie",
    "Haute-Savoie", "Paris", "Seine-Maritime", "Seine-et-Marne", "Yvelines",
    "Deux-Sevres", "Somme", "Tarn", "Tarn-et-Garonne", "Var", "Vaucluse",
    "Vendee", "Vienne", "Haute-Vienne", "Vosges", "Yonne",
    "Territoire-de-Belfort", "Essonne", "Hauts-de-Seine", "Seine-Saint-Denis",
    "Val-de-Marne", "Val-d-Oise",
]


# ── UTILS ────────────────────────────────────────────────────────
def classify_type(name_raw):
    name = name_raw.lower()
    if "vide-maison" in name or "vide maison" in name:
        return "vide-maison"
    if "vide-dressing" in name or "vide dressing" in name:
        return "vide-dressing"
    if "braderie" in name:
        return "braderie"
    if "foire" in name:
        return "foire"
    if "vide-grenier" in name or "vide grenier" in name:
        return "vide-greniers"
    if "brocante" in name or "puces" in name or "antiquit" in name:
        return "brocante"
    if "bourse" in name:
        return "brocante"
    return "brocante"


def estimate_size(text):
    if not text:
        return "medium"
    text = text.lower()
    m = re.search(r"(\d+)\s*(?:exposant|expos\.|stand|emplace)", text)
    if m:
        n = int(m.group(1))
        if n >= 80:
            return "large"
        if n >= 30:
            return "medium"
        return "small"
    if any(w in text for w in ["grand", "importante", "200", "150", "100"]):
        return "large"
    if "vide-maison" in text or "vide maison" in text:
        return "small"
    return "medium"


def extract_exposants(text):
    if not text:
        return ""
    m = re.search(r"(\d+)\s*(?:exposant|stand)", text, re.I)
    if m:
        return f"~{m.group(1)} exposants"
    return ""


def parse_hours_iso(start_str, end_str):
    try:
        s = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        sh = f"{s.hour}h" + (f"{s.minute:02d}" if s.minute else "")
        eh = f"{e.hour}h" + (f"{e.minute:02d}" if e.minute else "")
        return f"{sh} - {eh}"
    except (ValueError, AttributeError, TypeError):
        return ""


def parse_hours_text(text):
    if not text:
        return ""
    patterns = [
        r"(\d{1,2})\s*[hH]\s*(\d{2})?\s*[-/aà]\s*(\d{1,2})\s*[hH]\s*(\d{2})?",
        r"[Dd]e\s+(\d{1,2})\s*[hH]\s*(\d{2})?\s*[aà]\s*(\d{1,2})\s*[hH]\s*(\d{2})?",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            g = m.groups()
            sh = f"{int(g[0])}h" + (g[1] if g[1] else "")
            eh = f"{int(g[2])}h" + (g[3] if g[3] else "")
            return f"{sh} - {eh}"
    return ""


def parse_date_iso(s):
    """Parse YYYY-MM-DD or ISO datetime to date object."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def parse_date_fr(s):
    """Parse DD/MM/YYYY to date object."""
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def date_range(start_date, end_date, date_min, date_max):
    """Return list of days in [start_date, end_date] intersected with [date_min, date_max]."""
    days = []
    d = max(start_date, date_min)
    last = min(end_date, date_max)
    while d <= last:
        days.append(d)
        d += timedelta(days=1)
    return days


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def make_event(name, city, dept, address, lat, lon, event_date, hours,
               event_type, size, exposants, description, organizer, link, source):
    return {
        "name": name.strip(),
        "city": city.strip(),
        "dept": dept.strip(),
        "address": address.strip(),
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "date": str(event_date),
        "hours": hours,
        "type": event_type,
        "size": size,
        "exposants": exposants,
        "description": (description or "")[:300].strip(),
        "organizer": organizer,
        "link": link,
        "source": source,
    }


# ── HTTP ─────────────────────────────────────────────────────────
def fetch(url, retries=3, encoding=None):
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, timeout=20)
            if encoding:
                resp.encoding = encoding
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  [429] Rate limited, wait {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code == 403:
                print(f"  [403] Blocked for {url}")
                return None
            print(f"  [HTTP {resp.status_code}] {url}")
            return None
        except requests.RequestException as e:
            print(f"  [ERR] {e}")
            time.sleep(3)
    return None


# ── SOURCE 1: BROCABRAC.FR ──────────────────────────────────────
def scrape_brocabrac(date_min, date_max):
    print("\n" + "=" * 60)
    print("SOURCE 1: brocabrac.fr")
    print("=" * 60)
    all_events = []
    errors = []

    for i, dept in enumerate(DEPARTMENTS_NUM):
        print(f"  [{i+1}/{len(DEPARTMENTS_NUM)}] Dept {dept}...", end=" ", flush=True)
        try:
            events = _brocabrac_department(dept, date_min, date_max)
            print(f"{len(events)} events")
            all_events.extend(events)
        except Exception as e:
            print(f"ERREUR: {e}")
            errors.append(dept)
        time.sleep(1.0)

    if errors:
        print(f"  Erreurs: {', '.join(errors)}")
    print(f"  TOTAL brocabrac.fr: {len(all_events)}")
    return all_events


def _brocabrac_department(dept, date_min, date_max):
    events = []
    base_url = f"https://brocabrac.fr/{dept}"
    page = 1
    first_event_id = None

    while True:
        url = base_url if page == 1 else f"{base_url}?p={page}"
        html = fetch(url)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        ev_divs = soup.find_all("div", class_="ev", attrs={"data-event-id": True})
        if ev_divs:
            page_first_id = ev_divs[0].get("data-event-id")
            if page == 1:
                first_event_id = page_first_id
            elif page_first_id == first_event_id:
                break

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("@type") != "Event":
                continue
            events.extend(_brocabrac_process(data, date_min, date_max))

        if len(ev_divs) < 40:
            break
        page += 1
        if page > 10:
            break
        time.sleep(0.8)

    return events


def _brocabrac_process(data, date_min, date_max):
    start = data.get("startDate", "")
    if not start:
        return []
    start_date = parse_date_iso(start)
    if not start_date:
        return []

    end_str = data.get("endDate", "")
    end_date = parse_date_iso(end_str) if end_str else start_date
    if not end_date:
        end_date = start_date

    days = date_range(start_date, end_date, date_min, date_max)
    if not days:
        return []

    if "Cancelled" in data.get("eventStatus", ""):
        return []

    name = data.get("name", "").strip()
    if not name:
        return []

    loc = data.get("location", {})
    geo = loc.get("geo", {})
    try:
        lat = float(geo.get("latitude", 0))
        lon = float(geo.get("longitude", 0))
    except (ValueError, TypeError):
        return []
    if lat == 0 or lon == 0:
        return []

    addr = loc.get("address", {})
    city = addr.get("addressLocality", "")
    postal = addr.get("postalCode", "")
    dept = postal[:2] if postal else ""
    street = addr.get("streetAddress", "")
    full_addr = f"{street}, {postal} {city}".strip(", ")

    hours = parse_hours_iso(start, end_str)
    desc = (data.get("description") or "").strip()[:300]
    org = data.get("organizer", {})
    organizer = org.get("name", "") if isinstance(org, dict) else str(org) if org else ""
    combined = f"{name} {desc}"

    event_url = data.get("url", data.get("@id", ""))
    if event_url and not event_url.startswith("http"):
        event_url = "https://brocabrac.fr" + event_url

    results = []
    for day in days:
        results.append(make_event(
            name=name, city=city, dept=dept, address=full_addr,
            lat=lat, lon=lon, event_date=day, hours=hours,
            event_type=classify_type(name), size=estimate_size(combined),
            exposants=extract_exposants(combined), description=desc,
            organizer=organizer, link=event_url, source="brocabrac.fr",
        ))
    return results


# ── SOURCE 2: VIDE-GRENIERS.ORG ─────────────────────────────────
def scrape_videgreniers(date_min, date_max):
    print("\n" + "=" * 60)
    print("SOURCE 2: vide-greniers.org")
    print("=" * 60)
    all_events = []
    seen_ids = set()
    errors = []

    for i, dept_name in enumerate(VG_DEPARTMENTS):
        print(f"  [{i+1}/{len(VG_DEPARTMENTS)}] {dept_name}...", end=" ", flush=True)
        try:
            events, new_ids = _vg_department(dept_name, date_min, date_max, seen_ids)
            seen_ids.update(new_ids)
            print(f"{len(events)} events")
            all_events.extend(events)
        except Exception as e:
            print(f"ERREUR: {e}")
            errors.append(dept_name)
        time.sleep(1.0)

    if errors:
        print(f"  Erreurs: {', '.join(errors)}")
    print(f"  TOTAL vide-greniers.org: {len(all_events)}")
    return all_events


def _vg_department(dept_name, date_min, date_max, seen_ids):
    events = []
    new_ids = set()
    page = 1

    while page <= 8:  # securite
        url = f"https://vide-greniers.org/evenements/{dept_name}?page={page}"
        html = fetch(url)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        page_new = 0

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("@type") != "Event":
                continue

            # Dedup par ID
            event_id = data.get("@id", data.get("url", ""))
            m = re.search(r"/(\d+)/", event_id)
            eid = m.group(1) if m else event_id
            if eid in seen_ids or eid in new_ids:
                continue
            new_ids.add(eid)
            page_new += 1

            evs = _vg_process(data, date_min, date_max)
            events.extend(evs)

        # Si aucun nouvel event sur cette page, on arrete
        if page_new == 0:
            break
        page += 1
        time.sleep(0.8)

    return events, new_ids


def _vg_process(data, date_min, date_max):
    # Dates au format DD/MM/YYYY
    start_str = data.get("startDate", "")
    end_str = data.get("endDate", "")

    start_date = parse_date_fr(start_str) or parse_date_iso(start_str)
    if not start_date:
        return []
    end_date = parse_date_fr(end_str) or parse_date_iso(end_str) if end_str else start_date
    if not end_date:
        end_date = start_date

    days = date_range(start_date, end_date, date_min, date_max)
    if not days:
        return []

    status = data.get("eventStatus", "")
    if "Cancelled" in status:
        return []

    name = data.get("name", "").strip()
    if not name:
        return []

    loc = data.get("location", {})
    geo = loc.get("geo", {})
    try:
        lat = float(geo.get("latitude", 0))
        lon = float(geo.get("longitude", 0))
    except (ValueError, TypeError):
        return []
    if lat == 0 or lon == 0:
        return []

    addr = loc.get("address", {})
    # addressLocality format: "Ville-45"
    locality = addr.get("addressLocality", "")
    parts = locality.rsplit("-", 1)
    city = parts[0] if parts else locality
    dept = parts[1] if len(parts) > 1 and parts[1].isdigit() else ""

    venue = loc.get("name", "")
    full_addr = f"{venue}, {city}" if venue else city

    desc = (data.get("description") or "").strip()[:300]
    hours = parse_hours_text(desc)

    org = data.get("organizer", {})
    organizer = org.get("name", "") if isinstance(org, dict) else ""

    combined = f"{name} {desc}"
    event_url = data.get("url", data.get("@id", ""))

    results = []
    for day in days:
        results.append(make_event(
            name=name, city=city, dept=dept, address=full_addr,
            lat=lat, lon=lon, event_date=day, hours=hours,
            event_type=classify_type(name), size=estimate_size(combined),
            exposants=extract_exposants(combined), description=desc,
            organizer=organizer, link=event_url, source="vide-greniers.org",
        ))
    return results


# ── SOURCE 3: SABRADOU.COM ──────────────────────────────────────
_geocode_cache = {}


def _geocode(city, dept_num):
    """Geocode ville via adresse.data.gouv.fr. Cache en memoire."""
    key = f"{city}-{dept_num}"
    if key in _geocode_cache:
        return _geocode_cache[key]

    query = f"{city}"
    if dept_num:
        query += f" {dept_num}"
    try:
        resp = requests.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": query, "type": "municipality", "limit": 1},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            feats = data.get("features", [])
            if feats:
                coords = feats[0]["geometry"]["coordinates"]
                result = (coords[1], coords[0])  # lat, lon
                _geocode_cache[key] = result
                return result
    except Exception:
        pass
    _geocode_cache[key] = None
    return None


def scrape_sabradou(date_min, date_max):
    print("\n" + "=" * 60)
    print("SOURCE 3: sabradou.com")
    print("=" * 60)
    all_events = []

    # Generer les dates a scraper (format YYMMDD)
    d = date_min
    dates_to_scrape = []
    while d <= date_max:
        dates_to_scrape.append(d)
        d += timedelta(days=1)

    for target_date in dates_to_scrape:
        page_code = target_date.strftime("%y%m%d")
        url = f"https://www.sabradou.com/?page={page_code}"
        print(f"  [{target_date}] {url}...", end=" ", flush=True)

        html = fetch(url, encoding="iso-8859-1")
        if not html:
            print("skip")
            continue

        soup = BeautifulSoup(html, "html.parser")
        count = 0

        # Parcourir les blocs departement
        for section in soup.find_all("div", class_=["deptardt", "dept"]):
            # Extraire le departement du titre
            titre = section.find("li", class_="deptardt-titre")
            dept_num = ""
            if titre:
                m = re.match(r"(\d{2})", titre.get_text(strip=True))
                if m:
                    dept_num = m.group(1)

            for li in section.find_all("li"):
                if "deptardt-titre" in li.get("class", []):
                    continue
                a = li.find("a", href=True)
                if not a:
                    continue

                # Skip annule
                if li.find("span", class_="rouge"):
                    continue

                city_text = a.get_text(strip=True)
                event_type_text = a.get("title", "brocante")
                link = a["href"]
                if not link.startswith("http"):
                    link = "https://www.sabradou.com" + link

                # Pour "autres", le texte a le dept en prefixe: "01 Saint Jean"
                if not dept_num:
                    m = re.match(r"(\d{2})\s+(.+)", city_text)
                    if m:
                        dept_num = m.group(1)
                        city_text = m.group(2)

                # Geocoder la ville
                geo = _geocode(city_text, dept_num)
                if not geo:
                    continue
                lat, lon = geo

                # Type depuis le texte apres <br/>
                type_raw = event_type_text or "brocante"
                # Texte supplementaire dans le li
                li_text = li.get_text(" ", strip=True)

                ev = make_event(
                    name=f"{type_raw.capitalize()} - {city_text}",
                    city=city_text, dept=dept_num, address=city_text,
                    lat=lat, lon=lon, event_date=target_date, hours="",
                    event_type=classify_type(type_raw),
                    size=estimate_size(li_text),
                    exposants=extract_exposants(li_text),
                    description="", organizer="", link=link,
                    source="sabradou.com",
                )
                all_events.append(ev)
                count += 1

        print(f"{count} events")
        time.sleep(0.8)

    print(f"  TOTAL sabradou.com: {len(all_events)}")
    return all_events


# ── DEDUP ────────────────────────────────────────────────────────
def dedup_events(events):
    unique = []
    for ev in events:
        is_dup = False
        name_norm = re.sub(r"[^a-z0-9]", "", ev["name"].lower())
        for existing in unique:
            if ev["date"] != existing["date"]:
                continue
            existing_norm = re.sub(r"[^a-z0-9]", "", existing["name"].lower())
            dist = haversine(ev["lat"], ev["lon"], existing["lat"], existing["lon"])
            if dist < 0.5 and ev["type"] == existing["type"]:
                is_dup = True
                if len(ev.get("description", "")) > len(existing.get("description", "")):
                    existing["description"] = ev["description"]
                if ev["hours"] and not existing["hours"]:
                    existing["hours"] = ev["hours"]
                break
            if name_norm == existing_norm and ev["city"].lower() == existing["city"].lower():
                is_dup = True
                break
        if not is_dup:
            unique.append(ev)
    return unique


# ── MAIN ─────────────────────────────────────────────────────────
def main():
    today = date.today()
    date_min = today
    date_max = today + timedelta(days=DAYS_AHEAD)

    print("=" * 60)
    print("SCRAPER MULTI-SOURCES — France entiere")
    print(f"  Fenetre: {date_min} -> {date_max} ({DAYS_AHEAD} jours)")
    print(f"  Output: {OUTPUT}")
    print("=" * 60)

    events_1 = scrape_brocabrac(date_min, date_max)
    events_2 = scrape_videgreniers(date_min, date_max)
    events_3 = scrape_sabradou(date_min, date_max)

    all_events = events_1 + events_2 + events_3
    print(f"\n{'=' * 60}")
    print(f"TOTAL BRUT: {len(all_events)}")
    print(f"  brocabrac.fr:      {len(events_1)}")
    print(f"  vide-greniers.org: {len(events_2)}")
    print(f"  sabradou.com:      {len(events_3)}")

    all_events = dedup_events(all_events)
    print(f"APRES DEDUP: {len(all_events)}")

    all_events.sort(key=lambda e: e["date"])

    # Stats
    dates_count = {}
    types_count = {}
    sources_count = {}
    for ev in all_events:
        dates_count[ev["date"]] = dates_count.get(ev["date"], 0) + 1
        types_count[ev["type"]] = types_count.get(ev["type"], 0) + 1
        sources_count[ev["source"]] = sources_count.get(ev["source"], 0) + 1

    print("\nPar date:")
    for d, c in sorted(dates_count.items()):
        print(f"  {d}: {c}")
    print("\nPar type:")
    for t, c in sorted(types_count.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")
    print("\nPar source:")
    for s, c in sorted(sources_count.items(), key=lambda x: -x[1]):
        print(f"  {s}: {c}")

    # Ecrire le JSON
    output = {
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "date_min": str(date_min),
        "date_max": str(date_max),
        "total": len(all_events),
        "sources": ["brocabrac.fr", "vide-greniers.org", "sabradou.com"],
        "events": all_events,
    }

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(OUTPUT) / (1024 * 1024)
    print(f"\nSauvegarde: {OUTPUT} ({size_mb:.1f} Mo)")
    print("Done.")


if __name__ == "__main__":
    main()
