# Upload Portal — Kravspecifikation

## Sammanfattning

Webbaserad portal där kunder laddar upp leverantörsbilder, konfigurerar bearbetningsregler, och får tillbaka kvalitetssäkrade produktbilder. Pipelinen (Proc-Product-Photos) kör i bakgrunden. Operatören (vi) granskar och godkänner innan leverans.

**Målgrupp:** E-handlare med 1 000-50 000 produktbilder per säsong.

**Kärnflöde:**
```
Kund laddar upp  →  Pipeline kör  →  Operatör granskar  →  Kund laddar ner
```

---

## 1. Användarroller

### 1.1 Kund
- Registrerar konto (email + lösenord, eller SSO)
- Laddar upp bilder (ZIP, mapp, drag-and-drop)
- Väljer/skapar bearbetningsregler (kategorier, marginaler, canvas-storlek)
- Ser status på pågående jobb
- Laddar ner färdiga bilder
- Ser historik och statistik
- Hanterar sin mappningsfil (SKU → artikelnummer)

### 1.2 Operatör (vi)
- Ser alla kunders jobb i en dashboard
- Granskar kontaktark och flaggade bilder
- Gör manuell handpåläggning på flaggade
- Godkänner och markerar jobb som klara
- Justerar regler per kund
- Ser systemstatistik (total volym, svarstider, success rates)

### 1.3 Admin
- Hanterar kundkonton (skapa, stänga, ändra kvot)
- Konfigurerar prissättning/kvoter
- Systemkonfiguration

---

## 2. Kundflöde — detaljerat

### 2.1 Uppladdning

**Metoder:**
- Drag-and-drop i webbläsaren (enskilda filer eller ZIP)
- ZIP-fil upload (packas upp server-side)
- FTP/SFTP-katalog (för stora volymer, automatisk pickup)

**Stödda format:** TIFF, PNG, JPEG, BMP, WebP

**Filnamnsbegränsningar:**
- Max 255 tecken
- Mellanslag → bindestreck automatiskt
- Varning vid osäkra tecken

**Storlek:**
- Max filstorlek: 100 MB per fil
- Max batch: 50 000 filer eller 100 GB
- Progress bar under uppladdning

### 2.2 Jobbkonfiguration

Kunden konfigurerar innan bearbetning (eller använder sparade presets):

**Bildoutput:**
- Canvas-storlek (default 1000x1000, valbar: 500-4000)
- Output-format (JPG, PNG, WebP)
- JPEG-kvalitet (1-100, default 95)
- Bakgrundsfärg (default vit, valbar)

**Mappning:**
- Upload av mapping-fil (CSV/XLSX)
- Eller: automatisk — behåll originalfilnamn + suffix
- Eller: manuell mappning i portalen (drag-match UI)

**Kategorier:**
- Automatisk kategoritilldelning baserat på mapping
- Eller: kunden väljer per uppladdning ("detta är alla skor")
- Eller: blandade kategorier via mapping-kolumn

**Regler:**
- Välj preset ("Golf standard", "Sport standard", "Tight crop")
- Eller: per-kategori finjustering (marginaler, trösklar)
- Sparade presets per kund

**Namnkonvention:**
- Välj mönster: `{artikelnr}_{vinkel}.jpg`, `{original}-cropped.jpg`, etc.
- Preview av resulterande filnamn innan bearbetning

### 2.3 Bearbetning

**Status:**
- `Uppladdad` → `I kö` → `Bearbetas` → `Granskas` → `Klar` → `Nedladdad`
- Progress: "142/508 bilder klara (28%)"
- Estimerad tid kvar
- Push-notis (email) vid statusändringar

**Automatisk pipeline:**
- Kör Proc-Product-Photos med kundens regler
- Genererar kontaktark, stats, review-manifest
- Flaggade bilder markeras för operatörsgranskning

### 2.4 Preview och godkännande

**Kontaktark-vy:**
- Thumbnail-grid grupperat per kategori
- Klickbar — expandera till full storlek
- Grönt/rött/gult status per bild
- Filter: visa bara flaggade, visa bara recovered

**Bildpar-vy (before/after):**
- Original till vänster, croppat till höger
- Swipe eller slider för jämförelse

**Kunden kan:**
- Godkänna hela batchen
- Markera enskilda bilder för ombearbetning
- Lämna kommentarer på specifika bilder

### 2.5 Nedladdning

**Format:**
- ZIP med alla färdiga bilder
- Strukturerad ZIP (mappar per kategori)
- Enskild filnedladdning
- Direktlänk (giltig 30 dagar)

**Leveransnotis:**
- Email: "Dina 508 bilder är klara — ladda ner här"
- Inkluderar: success rate, antal, turnaround-tid

---

## 3. Operatörsflöde

### 3.1 Dashboard

**Översikt:**
- Aktiva jobb (per kund, status, progress)
- Kö-djup (väntande jobb)
- Senaste 7 dagars volym/success rate
- Flaggade bilder som väntar på granskning

**Per jobb:**
- Kundnamn, uppladdningstid, antal bilder
- Pipeline-status, success rate
- Kontaktark (inline eller klickbart)
- Lista på flaggade bilder med reason codes
- Knapp: "Godkänn och leverera"

### 3.2 Granskningsverktyg

**Flaggade bilder:**
- Side-by-side: original → mask → crop → final
- Reason code synlig
- Verktyg:
  - Godkänn som den är
  - Manuell crop-justering (enkel drag-rektangel)
  - Avvisa (exkludera från leverans)
  - Re-kör med annan kategori

**Batch-operationer:**
- "Godkänn alla recovered" (de som fallback fixade)
- "Avvisa alla failed" (de som inte gick att rädda)

### 3.3 Regelhantering per kund

- Kopiera/modifiera standard-regelpresets
- Per-kund YAML som sparas och versionshanteras
- A/B-jämförelse: kör med nya regler, diff mot gamla

---

## 4. Kvot- och faktureringssystem

### 4.1 Kvoter

**Modell:**
- Månadskvot i antal bilder (t.ex. 8 000 bilder/mån)
- Overage-pris per bild utöver kvot
- Rollover: oanvända bilder rullar inte över

**Spårning:**
- Realtidsräknare: "2 341 / 8 000 bilder använda denna månad"
- Varning vid 80% och 95% av kvot
- Automatiskt stopp eller overage vid 100%

### 4.2 Prissättning (konfigurerbara planer)

| Plan | Bilder/mån | Pris | Overage |
|------|-----------|------|---------|
| Starter | 2 000 | 4 000 kr | 2.50 kr/bild |
| Business | 8 000 | 10 000 kr | 1.50 kr/bild |
| Enterprise | 30 000 | 25 000 kr | 1.00 kr/bild |
| Custom | Förhandlat | Förhandlat | Förhandlat |

### 4.3 Fakturering

- Stripe-integration för kortbetalning
- Eller: manuell fakturering (B2B, 30 dagar netto)
- Månadsvis sammanställning
- Exporterbar användningsrapport

---

## 5. Teknisk arkitektur

### 5.1 Översikt

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Frontend    │────▶│  Backend API │────▶│  Processing      │
│  (React/     │◀────│  (FastAPI)   │◀────│  Worker          │
│   Next.js)   │     │              │     │  (vår pipeline)  │
└─────────────┘     └──────┬───────┘     └──────────────────┘
                           │
                    ┌──────┴───────┐
                    │  PostgreSQL  │
                    │  + S3/MinIO  │
                    └──────────────┘
```

### 5.2 Backend

**Framework:** FastAPI (Python — samma språk som pipelinen)

**API-endpoints:**
```
POST   /api/auth/register
POST   /api/auth/login
GET    /api/auth/me

POST   /api/jobs                    # Skapa nytt jobb
GET    /api/jobs                    # Lista mina jobb
GET    /api/jobs/{id}               # Jobb-detalj + status
POST   /api/jobs/{id}/upload        # Ladda upp filer
POST   /api/jobs/{id}/start         # Starta bearbetning
GET    /api/jobs/{id}/contact-sheet  # Hämta kontaktark
GET    /api/jobs/{id}/results       # Hämta per-bild resultat
GET    /api/jobs/{id}/download      # Hämta ZIP med färdiga bilder
POST   /api/jobs/{id}/approve       # Operatör godkänner
POST   /api/jobs/{id}/images/{img}/action  # Manuell åtgärd på bild

GET    /api/presets                  # Lista regelpresets
POST   /api/presets                  # Skapa/spara preset
PUT    /api/presets/{id}             # Uppdatera preset

GET    /api/account                  # Kontoinformation + kvot
GET    /api/account/usage            # Användningshistorik

GET    /api/admin/dashboard          # Operatörs-dashboard
GET    /api/admin/jobs               # Alla jobb (alla kunder)
POST   /api/admin/jobs/{id}/review   # Granska flaggade
```

### 5.3 Fillagring

**Uppladdade originalbilder:** S3-bucket eller MinIO (self-hosted)
**Bearbetade bilder:** Separat bucket, organiserat per jobb
**Kontaktark + previews:** Genereras on-demand eller cachade

**Retention:**
- Original: 90 dagar efter leverans (kunden kan ladda ner igen)
- Bearbetade: 30 dagar efter leverans
- Kontaktark/stats: permanent (liten storlek)

### 5.4 Jobbkö

**Celery + Redis** (eller enkel PostgreSQL-baserad kö):
- Jobb köas vid uppladdning
- Worker plockar jobb och kör pipelinen
- Status uppdateras i realtid via WebSocket

**Skalning:**
- En worker per CPU-kärna
- Parallellisering inom varje jobb (vår --parallel)
- Flera jobb kan köras samtidigt om kapacitet finns

### 5.5 Databas (PostgreSQL)

**Tabeller:**
```
users           (id, email, password_hash, role, plan, created_at)
jobs            (id, user_id, status, config_json, stats_json, created_at, completed_at)
job_images      (id, job_id, source_filename, output_filename, status, flags, metrics_json)
presets         (id, user_id, name, rules_yaml, created_at)
usage_records   (id, user_id, job_id, image_count, month, created_at)
invoices        (id, user_id, month, amount, status, stripe_id)
```

### 5.6 Frontend

**Framework:** Next.js (React) eller SvelteKit

**Sidor:**
```
/                       Landing page
/login                  Inloggning
/register               Registrering
/dashboard              Kundens jobb-översikt
/jobs/new               Ny uppladdning + konfiguration
/jobs/{id}              Jobb-detalj, status, preview
/jobs/{id}/review       Bildpar-vy, godkännande
/jobs/{id}/download     Nedladdning
/settings               Konto, presets, mappningsfiler
/admin                  Operatörs-dashboard
/admin/jobs/{id}        Operatörs-granskning
```

**Komponenter:**
- Drag-and-drop upload med progress
- Thumbnail-grid (kontaktark i webbläsaren)
- Before/after slider
- Regelkonfigurator (formulär med live preview)
- Jobbstatus med realtidsuppdatering (WebSocket)
- Kvotmätare

---

## 6. Säkerhet

- HTTPS everywhere
- JWT-tokens med refresh
- Filuppladdning: virusscan, filtypsvalidering, storleksbegränsning
- Kunddata isolerad (tenant-separation i queries)
- Lösenord: bcrypt/argon2
- Rate limiting på API
- GDPR: kunden kan radera sitt konto och alla bilder

---

## 7. Implementationsfaser

### Fas 1 — MVP (40-60h)
- Grundläggande auth (login/register)
- Fil-upload (ZIP + drag-drop)
- Konfiguration (canvas-storlek, format, kategori)
- Pipeline-integration (kör jobb, visa status)
- Kontaktark-vy
- Nedladdning (ZIP)
- Operatörs-dashboard (lista jobb, godkänn)
- Deployment (enkel VPS eller Docker)

### Fas 2 — Produktion (30-40h)
- Betalningsintegration (Stripe)
- Kvotsystem
- Email-notiser
- Regelpresets per kund
- Before/after preview
- Manuell crop-justering i granskning
- FTP/SFTP pickup

### Fas 3 — Skalning (20-30h)
- Jobbkö med Celery
- Flera workers
- S3-lagring
- Usage dashboard
- A/B-regeljämförelse (diff-rapport i portalen)
- API-nycklar för programmatisk access
- Webhook-notiser

---

## 8. Hosting och drift

### Alternativ 1 — Enkel start (Fas 1)
- En VPS (Hetzner, 8 kärnor, 32 GB, ~500 kr/mån)
- Docker Compose: backend + frontend + PostgreSQL + MinIO
- Nginx som reverse proxy
- Let's Encrypt SSL

### Alternativ 2 — Skalbar (Fas 2-3)
- Kubernetes eller Docker Swarm
- Managed PostgreSQL (Supabase, Neon, eller AWS RDS)
- S3 för fillagring
- Separat worker-noder (kan skala horisontellt)

### Uppskattad driftkostnad
- Fas 1: ~500-1 000 kr/mån (en VPS)
- Fas 2: ~1 500-3 000 kr/mån (managed DB + storage)
- Fas 3: ~3 000-8 000 kr/mån (beroende på volym)

---

## 9. Icke-funktionella krav

| Krav | Mål |
|------|-----|
| Uppladdningshastighet | Begränsas av kundens internet, inte servern |
| Bearbetningstid | < 1h för 10 000 bilder |
| Turnaround SLA | 48h (inkl. manuell granskning) |
| Tillgänglighet | 99.5% (exkl. planerat underhåll) |
| Datasäkerhet | Kundbilder inte åtkomliga för andra kunder |
| Backup | Daglig databasbackup, bilder på S3 med versioning |
| GDPR | Radering av kunddata inom 30 dagar på begäran |
