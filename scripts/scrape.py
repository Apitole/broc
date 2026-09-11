#!/usr/bin/env python3
"""
Scraper brocabrac.fr — Toute la France, 14 jours glissants.
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

# Tous les departements metropolitains
DEPARTMENTS = [
    f"{i:02d}" for i in range(1, 20)
] + ["2A", "2B"] + [
    f"{i}" for i in range(21, 96)
]

BASE_URL = "https://brocabrac.fr"
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


def parse_hours(start_str, end_str):
    try:
        s = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        sh = f"{s.hour}h" + (f"{s.minute:02d}" if s.minute else "")
        eh = f"{e.hour}h" + (f"{e.minute:02d}" if e.minute else "")
        return f"{sh} - {eh}"
    except (ValueError, AttributeError, TypeError):
        return ""


# ── SCRAPING ─────────────────────────────────────────────────────
def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, timeout=20)
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


def scrape_department(dept, date_min, date_max):
    """Scrape toutes les pages d'un departement, filtre par fenetre de dates."""
    events = []
    base_url = f"{BASE_URL}/{dept}"
    page = 1
    first_event_id = None

    while True:
        url = base_url if page == 1 else f"{base_url}?p={page}"
        html = fetch(url)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")

        # Detection de wrap-around via data-event-id
        ev_divs = soup.find_all("div", class_="ev", attrs={"data-event-id": True})
        if not ev_divs:
            # Pas de div.ev ? Essayer quand meme les JSON-LD
            pass
        else:
            page_first_id = ev_divs[0].get("data-event-id")
            if page == 1:
                first_event_id = page_first_id
            elif page_first_id == first_event_id:
                break  # wrap-around

        # Extraire JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict) or data.get("@type") != "Event":
                continue

            evs = process_jsonld(data, date_min, date_max)
            events.extend(evs)

        # Pagination: si moins de 40 resultats, c'est la derniere page
        if len(ev_divs) < 40:
            break
        page += 1
        if page > 10:  # securite
            break
        time.sleep(0.8)

    return events


def process_jsonld(data, date_min, date_max):
    """Convertit un event JSON-LD en liste de dicts (un par jour couvert)."""
    start = data.get("startDate", "")
    if not start:
        return []

    # Parser start date
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        start_date = dt.date()
    except ValueError:
        try:
            start_date = date.fromisoformat(start[:10])
        except ValueError:
            return []

    # Parser end date (peut etre un autre jour)
    end_str = data.get("endDate", "")
    end_date = start_date
    if end_str:
        try:
            edt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            end_date = edt.date()
        except ValueError:
            try:
                end_date = date.fromisoformat(end_str[:10])
            except ValueError:
                end_date = start_date

    # Generer la liste de jours couverts (dans la fenetre)
    event_days = []
    d = max(start_date, date_min)
    last = min(end_date, date_max)
    while d <= last:
        event_days.append(d)
        d += timedelta(days=1)

    if not event_days:
        return []

    # Skip annules
    if "Cancelled" in data.get("eventStatus", ""):
        return []

    name = data.get("name", "").strip()
    if not name:
        return []

    # Geo
    loc = data.get("location", {})
    geo = loc.get("geo", {})
    try:
        lat = float(geo.get("latitude", 0))
        lon = float(geo.get("longitude", 0))
    except (ValueError, TypeError):
        return []
    if lat == 0 or lon == 0:
        return []

    # Adresse
    addr = loc.get("address", {})
    city = addr.get("addressLocality", "")
    postal = addr.get("postalCode", "")
    dept = postal[:2] if postal else ""
    street = addr.get("streetAddress", "")
    full_addr = f"{street}, {postal} {city}".strip(", ")

    # Horaires
    hours = parse_hours(start, end_str)

    # Description + organisateur
    desc = (data.get("description") or "").strip()[:300]
    org = data.get("organizer", {})
    organizer = org.get("name", "") if isinstance(org, dict) else str(org) if org else ""

    # Type et taille
    combined = f"{name} {desc}"
    event_type = classify_type(name)
    size = estimate_size(combined)

    # Exposants
    exposants = ""
    m = re.search(r"(\d+)\s*(?:exposant|stand)", combined, re.I)
    if m:
        exposants = f"~{m.group(1)} exposants"

    # URL
    event_url = data.get("url", data.get("@id", ""))
    if event_url and not event_url.startswith("http"):
        event_url = BASE_URL + event_url

    # Un event par jour couvert
    results = []
    for day in event_days:
        results.append({
            "name": name,
            "city": city,
            "dept": dept,
            "address": full_addr,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "date": str(day),
            "hours": hours,
            "type": event_type,
            "size": size,
            "exposants": exposants,
            "description": desc,
            "organizer": organizer,
            "link": event_url,
        })
    return results


# ── DEDUP ────────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


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
                # Garder la meilleure description
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
    print("SCRAPER BROCABRAC.FR — France entiere")
    print(f"  Fenetre: {date_min} -> {date_max} ({DAYS_AHEAD} jours)")
    print(f"  Departements: {len(DEPARTMENTS)}")
    print(f"  Output: {OUTPUT}")
    print("=" * 60)

    all_events = []
    errors = []

    for i, dept in enumerate(DEPARTMENTS):
        print(f"\n[{i+1}/{len(DEPARTMENTS)}] Dept {dept}...", end=" ", flush=True)
        try:
            events = scrape_department(dept, date_min, date_max)
            print(f"{len(events)} events")
            all_events.extend(events)
        except Exception as e:
            print(f"ERREUR: {e}")
            errors.append(dept)
        time.sleep(1.0)

    print(f"\n{'=' * 60}")
    print(f"Total brut: {len(all_events)} events")

    # Dedup
    all_events = dedup_events(all_events)
    print(f"Apres dedup: {len(all_events)} events")

    # Tri par date
    all_events.sort(key=lambda e: e["date"])

    # Stats
    dates_count = {}
    types_count = {}
    for ev in all_events:
        dates_count[ev["date"]] = dates_count.get(ev["date"], 0) + 1
        types_count[ev["type"]] = types_count.get(ev["type"], 0) + 1

    print("\nPar date:")
    for d, c in sorted(dates_count.items()):
        print(f"  {d}: {c}")
    print("\nPar type:")
    for t, c in sorted(types_count.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")

    if errors:
        print(f"\nErreurs sur: {', '.join(errors)}")

    # Ecrire le JSON
    output = {
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "date_min": str(date_min),
        "date_max": str(date_max),
        "total": len(all_events),
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
