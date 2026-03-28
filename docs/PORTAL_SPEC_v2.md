# Upload Portal v2 — Teknisk Specifikation

## Syfte

Kundvänd webbportal för:
- Landing page / sälj
- Kontohantering + login
- Bilduppladdning + jobbkonfiguration
- Jobbstatus + kontaktark-preview
- Nedladdning av färdiga bilder
- Operatörs-dashboard för QA

**Ej i scope (v1):** Bildbehandlings-UI (crop-justering, preview-slider).
Det kommer i v2 när kärnflödet är validerat.

---

## Repos och deras ansvar

```
Proc-Product-Photos    — Pipeline (CLI, bildbearbetning, crop-logik)
Proc-Product-License   — Licensserver (kvoter, API-nycklar, fakturering)
Proc-Product-Portal    — Webbportal (detta dokument)
```

Portal pratar med License Server för kvot/kund-data.
Portal anropar Pipeline som subprocess eller worker.

---

## Tech Stack

| Komponent | Val | Motivering |
|-----------|-----|------------|
| Frontend | Next.js 14+ (App Router) | SSR, React-ekosystem, stort community |
| Styling | Tailwind CSS | Snabbt, konsekvent, ingen CSS-overhead |
| UI-komponenter | shadcn/ui | Headless, kopierbart, ej dependency |
| Backend API | Next.js API Routes + FastAPI | Enkla routes i Next, tunga jobb i FastAPI |
| Databas | PostgreSQL (delad med License Server) | En databas, schema-separation |
| Fillagring | S3 / MinIO | Uppladdade + bearbetade bilder |
| Jobbkö | Bull (Redis) eller Celery | Asynkron pipeline-körning |
| Auth | NextAuth.js | OAuth, credentials, JWT |
| Deployment | Docker Compose (fas 1) | Enkelt, sedan K8s om nödvändigt |

---

## Sidstruktur

### Publika sidor

```
/                           Landing page (sälj)
/pricing                    Prisplaner + jämförelse
/about                      Om tjänsten
/login                      Inloggning
/register                   Registrering
/terms                      Villkor
/privacy                    Integritetspolicy
```

### Kundsidor (autentiserade)

```
/dashboard                  Översikt: senaste jobb, kvot, snabbstatus
/jobs                       Lista alla jobb
/jobs/new                   Ny uppladdning + konfiguration
/jobs/[id]                  Jobb-detalj: status, progress, kontaktark
/jobs/[id]/download         Nedladdningssida
/settings                   Kontoinställningar
/settings/presets           Bearbetningspresets
/settings/mappings          Sparade mappningsfiler
/settings/api-keys          API-nycklar för lokal pipeline
/billing                    Kvot, användning, fakturor
```

### Operatörssidor

```
/admin                      Dashboard: alla jobb, systemstatus
/admin/jobs                 Alla jobb (alla kunder)
/admin/jobs/[id]            Granska jobb: kontaktark, flaggade bilder
/admin/customers            Kundlista
/admin/customers/[id]       Kunddetalj + usage
/admin/billing              Faktureringsöversikt
/admin/plans                Hantera prisplaner
```

---

## Sida-för-sida specifikation

### Landing page (/)

**Syfte:** Konvertera besökare till registrering.

**Sektioner:**
1. Hero: "Professionella produktbilder på minuter, inte dagar"
   - Kort beskrivning
   - CTA: "Prova gratis" / "Se priser"
   - Before/after slider med exempelbild

2. Hur det fungerar (3 steg):
   - Ladda upp → Vi bearbetar → Ladda ner
   - Ikoner + kort text

3. Kategorier vi stöder:
   - Grid med produktbilder (golf, sport, skor, etc.)
   - "Kategorimedveten cropping" som USP

4. Priser:
   - 3 planer (Starter / Business / Enterprise)
   - "Spara 80% jämfört med manuell bearbetning"
   - CTA per plan

5. Kundcitat:
   - Dimbo (när vi har tillåtelse)

6. FAQ

7. Footer: kontakt, villkor, integritetspolicy

### Ny uppladdning (/jobs/new)

**Flöde:**

```
Steg 1: Upload
┌──────────────────────────────────────┐
│                                      │
│   Dra och släpp filer här            │
│   eller klicka för att välja         │
│                                      │
│   Stöd: TIFF, PNG, JPEG, WebP, BMP  │
│   Max: 50 000 filer / 100 GB        │
│                                      │
│   ┌────────────────────────────┐     │
│   │ supplier_images.zip  4.2GB │ ✓   │
│   │ ████████████████████ 100%  │     │
│   └────────────────────────────┘     │
│                                      │
│   508 bilder identifierade           │
│                                      │
│   [Nästa →]                          │
└──────────────────────────────────────┘

Steg 2: Konfiguration
┌──────────────────────────────────────┐
│                                      │
│   Preset: [▼ Golf Standard      ]   │
│                                      │
│   Canvas:     [1000] × [1000] px     │
│   Format:     [▼ JPG ]              │
│   Kvalitet:   [95] (1-100)          │
│   Bakgrund:   [█ Vit]               │
│                                      │
│   Mappning:   ○ Behåll originalnamn  │
│               ○ Ladda upp CSV/XLSX   │
│               ● Automatisk + suffix  │
│                                      │
│   Namnmönster: [{original}-cropped]  │
│   Preview:     IMG001-cropped.jpg    │
│                                      │
│   Kvot: 508 bilder (18 420 kvar)    │
│                                      │
│   [← Tillbaka]  [Starta bearbetning]│
└──────────────────────────────────────┘

Steg 3: Status (redirect till /jobs/[id])
```

### Jobb-detalj (/jobs/[id])

```
┌──────────────────────────────────────────────────┐
│ Jobb: Upload 2026-03-28                          │
│ Status: ● Bearbetas (342/508 — 67%)             │
│ ████████████████████░░░░░░░░░░ 67%               │
│ Estimerad tid kvar: ~2 min                       │
│                                                  │
│ ┌──────────┬──────────┬──────────┬─────────────┐ │
│ │ 342 OK   │ 12 Recov │ 0 Flag   │ 2 Failed   │ │
│ └──────────┴──────────┴──────────┴─────────────┘ │
│                                                  │
│ [Kontaktark]  [Statistik]  [Ladda ner ↓]        │
│                                                  │
│ Kontaktark (thumbnail-grid):                     │
│ ┌──┬──┬──┬──┬──┬──┬──┬──┬──┬──┐                 │
│ │  │  │  │  │  │  │  │  │  │  │ BAG (346)       │
│ ├──┼──┼──┼──┼──┼──┼──┼──┼──┼──┤                 │
│ │  │  │  │  │  │  │  │  │  │  │                  │
│ └──┴──┴──┴──┴──┴──┴──┴──┴──┴──┘                 │
│                                                  │
│ Klicka på thumbnail för full storlek             │
└──────────────────────────────────────────────────┘
```

### Dashboard (/dashboard)

```
┌──────────────────────────────────────────────────┐
│ Välkommen, Dimbo Golf                            │
│                                                  │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐             │
│ │  6 580  │ │ 18 420  │ │  99.6%  │             │
│ │ Använda │ │  Kvar   │ │ Success │             │
│ └─────────┘ └─────────┘ └─────────┘             │
│                                                  │
│ Senaste jobb:                                    │
│ ┌────────────────────────────────────────────┐   │
│ │ 28 mar  508 bilder  ● Klar    [Ladda ner] │   │
│ │ 15 mar  2100 bilder ● Klar    [Ladda ner] │   │
│ │ 3 feb   945 bilder  ● Klar    [Ladda ner] │   │
│ └────────────────────────────────────────────┘   │
│                                                  │
│ [+ Ny uppladdning]                               │
└──────────────────────────────────────────────────┘
```

### Operatörs-dashboard (/admin)

```
┌──────────────────────────────────────────────────┐
│ Operatör Dashboard                               │
│                                                  │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐│
│ │   12    │ │    3    │ │ 42 300  │ │ 87 500  ││
│ │ Kunder  │ │ I kö    │ │ Bilder  │ │ kr/mån  ││
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘│
│                                                  │
│ Kräver granskning:                               │
│ ┌────────────────────────────────────────────┐   │
│ │ ⚠ Dimbo Golf — 3 flaggade bilder          │   │
│ │   508 bilder, 99.4% success   [Granska →] │   │
│ ├────────────────────────────────────────────┤   │
│ │ ⚠ SportAB — 12 flaggade bilder            │   │
│ │   2100 bilder, 99.2% success  [Granska →] │   │
│ └────────────────────────────────────────────┘   │
│                                                  │
│ Alla jobb:                                       │
│ [Klar ▼] [Granskas ▼] [Bearbetas ▼] [I kö ▼]  │
└──────────────────────────────────────────────────┘
```

---

## Jobblivscykel

```
                ┌─────────┐
                │ UPLOADED │  ← Filer uppladdade, ej startad
                └────┬────┘
                     │ Kund klickar "Starta"
                     ▼
                ┌─────────┐
                │ QUEUED   │  ← I kö, väntar på worker
                └────┬────┘
                     │ Worker plockar jobb
                     ▼
                ┌────────────┐
                │ PROCESSING │  ← Pipeline kör
                └────┬───────┘
                     │ Pipeline klar
                     ▼
              ┌──────┴───────┐
              │              │
         0 flaggade     1+ flaggade
              │              │
              ▼              ▼
        ┌──────────┐  ┌───────────┐
        │ COMPLETE │  │ REVIEWING │  ← Operatör granskar
        └────┬─────┘  └─────┬─────┘
             │               │ Operatör godkänner
             │               ▼
             │         ┌──────────┐
             │         │ COMPLETE │
             │         └────┬─────┘
             ▼              ▼
        ┌──────────────────────┐
        │ Kund notifieras      │
        │ Nedladdning möjlig   │
        └──────────────────────┘
```

Jobb utan flaggade bilder slipper operatörsgranskning — automatisk leverans.

---

## Projektstruktur

```
Proc-Product-Portal/
├── package.json
├── next.config.js
├── tailwind.config.js
├── Dockerfile
├── docker-compose.yml          # Portal + Redis + Worker
├── .env.example
│
├── src/
│   ├── app/                    # Next.js App Router
│   │   ├── layout.tsx          # Root layout
│   │   ├── page.tsx            # Landing page
│   │   ├── pricing/page.tsx
│   │   ├── login/page.tsx
│   │   ├── register/page.tsx
│   │   │
│   │   ├── dashboard/
│   │   │   └── page.tsx
│   │   ├── jobs/
│   │   │   ├── page.tsx        # Lista jobb
│   │   │   ├── new/page.tsx    # Ny uppladdning
│   │   │   └── [id]/
│   │   │       ├── page.tsx    # Jobb-detalj
│   │   │       └── download/page.tsx
│   │   ├── settings/
│   │   │   ├── page.tsx
│   │   │   ├── presets/page.tsx
│   │   │   ├── api-keys/page.tsx
│   │   │   └── mappings/page.tsx
│   │   ├── billing/page.tsx
│   │   │
│   │   ├── admin/
│   │   │   ├── page.tsx        # Operatörs-dashboard
│   │   │   ├── jobs/
│   │   │   │   ├── page.tsx
│   │   │   │   └── [id]/page.tsx
│   │   │   ├── customers/
│   │   │   │   ├── page.tsx
│   │   │   │   └── [id]/page.tsx
│   │   │   └── billing/page.tsx
│   │   │
│   │   └── api/                # Next.js API routes
│   │       ├── auth/[...nextauth]/route.ts
│   │       ├── jobs/route.ts
│   │       ├── jobs/[id]/route.ts
│   │       ├── jobs/[id]/upload/route.ts
│   │       ├── jobs/[id]/start/route.ts
│   │       ├── jobs/[id]/contact-sheet/route.ts
│   │       └── jobs/[id]/download/route.ts
│   │
│   ├── components/
│   │   ├── ui/                 # shadcn components
│   │   ├── upload-dropzone.tsx
│   │   ├── job-config-form.tsx
│   │   ├── contact-sheet-viewer.tsx
│   │   ├── job-status-badge.tsx
│   │   ├── quota-meter.tsx
│   │   ├── thumbnail-grid.tsx
│   │   ├── pricing-card.tsx
│   │   └── before-after-slider.tsx
│   │
│   ├── lib/
│   │   ├── license-client.ts   # Pratar med License Server
│   │   ├── pipeline-worker.ts  # Startar pipeline-jobb
│   │   ├── s3.ts               # Filuppladdning/-nedladdning
│   │   ├── db.ts               # Prisma/Drizzle client
│   │   └── auth.ts             # NextAuth config
│   │
│   └── types/
│       ├── job.ts
│       ├── customer.ts
│       └── plan.ts
│
├── worker/                     # Pipeline-worker (Python)
│   ├── worker.py               # Lyssnar på jobbkö
│   ├── requirements.txt        # Inkl. proc-product-photos
│   └── Dockerfile
│
└── prisma/                     # Eller Drizzle
    └── schema.prisma           # Portal-specifika tabeller
```

---

## Datamodell (portal-specifik)

Utöver License Server-tabellerna:

```sql
-- Portal-specifik
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     UUID REFERENCES customers(id),
    license_id      UUID REFERENCES licenses(id),
    status          TEXT DEFAULT 'uploaded',  -- uploaded|queued|processing|reviewing|complete|failed
    config_json     JSONB,                    -- canvas_size, format, preset, etc.
    source_count    INTEGER DEFAULT 0,
    processed_count INTEGER DEFAULT 0,
    success_count   INTEGER DEFAULT 0,
    flagged_count   INTEGER DEFAULT 0,
    failed_count    INTEGER DEFAULT 0,
    stats_json      JSONB,                    -- full pipeline stats
    storage_key     TEXT,                     -- S3 prefix för detta jobb
    contact_sheet_key TEXT,                   -- S3 key för kontaktark
    created_at      TIMESTAMPTZ DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    reviewed_at     TIMESTAMPTZ,
    reviewed_by     UUID REFERENCES users(id)
);

CREATE TABLE job_images (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES jobs(id),
    source_filename TEXT NOT NULL,
    output_filename TEXT,
    status          TEXT,                     -- ok|recovered|flagged|failed
    flags           TEXT[],
    category        TEXT,
    fill_ratio      NUMERIC(5,4),
    operator_action TEXT,                     -- null|approved|rejected|reprocessed
    operator_note   TEXT
);
```

---

## Implementationsfaser

### Fas 1 — MVP (40-50h)
- Next.js skeleton + Tailwind + shadcn/ui
- Landing page (statisk, sälj-optimerad)
- Auth (NextAuth, email+password)
- Upload-flöde: drag-drop → config → starta
- Pipeline-worker: plocka jobb, kör pipeline, uppdatera status
- Jobb-detalj med progress + kontaktark-bild
- ZIP-nedladdning
- Grundläggande operatörs-dashboard
- License Server-integration (kvotkontroll)
- Docker Compose deployment

### Fas 2 — Produktion (25-35h)
- Pricing-sida med Stripe Checkout
- Kvotmätare i dashboard
- Email-notiser (jobb klart, kvot 80%)
- Presets (spara/ladda regelkonfigurationer)
- Operatör: godkänn/avvisa flaggade bilder
- Usage-historik + faktureringsvy
- FTP/SFTP pickup (optional)

### Fas 3 — Polish + Skalning (20-30h)
- Before/after slider på jobb-detalj
- Klickbara thumbnails i kontaktark
- API-nyckel hantering (för lokal pipeline)
- Webhook-notiser
- Multi-worker skalning
- CDN för nedladdningar
- Förbättrad landing page (animationer, testimonials)
