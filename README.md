# Brocantes Carte

Trouve les brocantes et vide-greniers pres de chez toi dans les 14 prochains jours.
Site 100% statique heberge sur Vercel, donnees mises a jour chaque matin par GitHub Actions.

## Comment ca marche

1. **Chaque matin a 7h**, un cron GitHub Actions scrape [brocabrac.fr](https://brocabrac.fr) pour tous les departements francais (14 jours glissants)
2. Le JSON genere (`public/events.json`) est commit dans le repo
3. Vercel deploie automatiquement au push
4. L'utilisateur entre sa **ville** + un **rayon** → le filtrage se fait **cote client** (haversine JS)

Zero serverless function, zero base de donnees, zero probleme de concurrence.

## Structure

```
├── public/
│   ├── index.html          # Frontend (Leaflet + recherche ville + filtres)
│   └── events.json         # Donnees scrapees (genere par le cron)
├── scripts/
│   └── scrape.py           # Scraper brocabrac.fr (96 departements)
├── .github/
│   └── workflows/
│       └── scrape.yml      # Cron quotidien (GitHub Actions)
├── _archive/               # Anciens scripts (gitignored)
├── vercel.json             # Config Vercel (sert public/)
├── requirements.txt        # Dependencies Python (scraper)
└── .gitignore
```

## Setup

### 1. GitHub repo

```bash
git init
git add .
git commit -m "init: brocantes carte"
gh repo create brocantes-carte --public --push
```

### 2. Premier scrape (local)

```bash
pip install -r requirements.txt
python scripts/scrape.py
```

Cela genere `public/events.json`. Commit et push.

### 3. Vercel

- Importer le repo sur [vercel.com](https://vercel.com)
- Framework preset: **Other**
- Output directory: `public`
- C'est tout, le site est live

### 4. Cron automatique

Le workflow `.github/workflows/scrape.yml` tourne chaque matin a 5h UTC.
Il scrape, commit `events.json`, et Vercel re-deploie automatiquement.

Pour lancer manuellement : Actions > "Scrape brocabrac.fr" > Run workflow.

## Stack

| Composant | Techno |
|---|---|
| Frontend | HTML/JS vanilla, Leaflet, API adresse.data.gouv.fr |
| Donnees | JSON statique, scrape quotidien |
| Scraping | Python (requests + BeautifulSoup) |
| Cron | GitHub Actions |
| Hosting | Vercel (static) |
