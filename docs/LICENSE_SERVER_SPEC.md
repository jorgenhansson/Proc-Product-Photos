# Licensserver — Teknisk Specifikation

## Syfte

Fristående API-tjänst som hanterar:
- Kundlicenser och API-nycklar
- Kvotkontroll (årskvot med rollover)
- Usage-tracking per kund
- Faktureringsunderlag
- Heartbeat från lokala pipeline-installationer

Används av både webbportalen och lokala pipeline-klienter.

---

## Arkitektur

```
Pipeline (lokal)          Portal (webb)           Admin
      │                        │                    │
      ▼                        ▼                    ▼
┌──────────────────────────────────────────────────────┐
│                  License Server API                   │
│                  (FastAPI + PostgreSQL)                │
│                                                       │
│  /api/v1/license/validate    ← kontrollera licens     │
│  /api/v1/usage/report        ← rapportera användning  │
│  /api/v1/usage/quota         ← kolla kvarvarande kvot │
│  /api/v1/customers/*         ← kundhantering          │
│  /api/v1/plans/*             ← prisplaner             │
│  /api/v1/admin/*             ← dashboard-data         │
└──────────────────────────────────────────────────────┘
                        │
                  ┌─────┴─────┐
                  │ PostgreSQL │
                  └───────────┘
```

---

## Datamodell

### customers
```sql
CREATE TABLE customers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    company         TEXT,
    phone           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    active          BOOLEAN DEFAULT true,
    notes           TEXT
);
```

### plans
```sql
CREATE TABLE plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,           -- "Starter", "Business", "Enterprise"
    annual_quota    INTEGER NOT NULL,        -- bilder per år
    monthly_price   NUMERIC(10,2) NOT NULL,  -- SEK
    overage_price   NUMERIC(10,2) NOT NULL,  -- SEK per bild utöver kvot
    active          BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### licenses
```sql
CREATE TABLE licenses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     UUID REFERENCES customers(id),
    plan_id         UUID REFERENCES plans(id),
    api_key         TEXT UNIQUE NOT NULL,    -- slumpad nyckel
    api_secret_hash TEXT NOT NULL,           -- bcrypt-hashad secret
    starts_at       DATE NOT NULL,
    expires_at      DATE NOT NULL,           -- starts_at + 12 månader
    annual_quota    INTEGER NOT NULL,        -- kopierat från plan vid skapande
    active          BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### usage_events
```sql
CREATE TABLE usage_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    license_id      UUID REFERENCES licenses(id),
    image_count     INTEGER NOT NULL,
    categories      JSONB,                   -- {"BAG": 120, "SHOE": 45}
    success_rate    NUMERIC(5,4),
    source          TEXT DEFAULT 'local',    -- 'local' | 'portal'
    job_id          UUID,                    -- referens till portal-jobb om relevant
    reported_at     TIMESTAMPTZ DEFAULT now()
);
```

### invoices
```sql
CREATE TABLE invoices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     UUID REFERENCES customers(id),
    license_id      UUID REFERENCES licenses(id),
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    base_amount     NUMERIC(10,2) NOT NULL,  -- månadsavgift
    overage_images  INTEGER DEFAULT 0,
    overage_amount  NUMERIC(10,2) DEFAULT 0,
    total_amount    NUMERIC(10,2) NOT NULL,
    status          TEXT DEFAULT 'draft',     -- draft | sent | paid | overdue
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

---

## API-endpoints

### Autentisering

Alla API-anrop autentiseras med `X-API-Key` header.
Portal-anrop använder JWT Bearer token istället.

### Licens

```
POST /api/v1/license/validate
  Request:  { "api_key": "..." }
  Response: { "valid": true, "customer": "Dimbo Golf",
              "plan": "Business", "expires_at": "2027-03-28",
              "quota_remaining": 18420 }

  Anropas av pipeline vid start. Cachebar i 1h.
```

### Kvot

```
GET /api/v1/usage/quota
  Headers:  X-API-Key: ...
  Response: { "annual_quota": 25000, "used": 6580,
              "remaining": 18420, "period_ends": "2027-03-28",
              "overage_price": 4.00 }
```

### Usage-rapportering

```
POST /api/v1/usage/report
  Headers:  X-API-Key: ...
  Request:  { "image_count": 508,
              "categories": {"BAG": 346, "BALL": 45, "SHOE": 21, ...},
              "success_rate": 0.996,
              "processing_time_s": 437,
              "source": "local" }
  Response: { "accepted": true, "quota_remaining": 17912 }

  Anropas av pipeline efter varje körning.
```

### Kundhantering (admin/portal)

```
POST   /api/v1/customers              # Skapa kund
GET    /api/v1/customers              # Lista kunder
GET    /api/v1/customers/{id}         # Kunddetalj
PUT    /api/v1/customers/{id}         # Uppdatera kund
DELETE /api/v1/customers/{id}         # Inaktivera kund

POST   /api/v1/customers/{id}/license # Skapa licens
GET    /api/v1/customers/{id}/license # Hämta aktiv licens
GET    /api/v1/customers/{id}/usage   # Användningshistorik
GET    /api/v1/customers/{id}/invoices # Fakturor
```

### Planer

```
GET    /api/v1/plans                  # Lista prisplaner
POST   /api/v1/plans                  # Skapa plan (admin)
PUT    /api/v1/plans/{id}             # Uppdatera plan (admin)
```

### Admin-dashboard

```
GET /api/v1/admin/dashboard
  Response: {
    "total_customers": 12,
    "active_licenses": 10,
    "images_this_month": 42300,
    "revenue_this_month": 87500,
    "top_customers": [...],
    "usage_trend": [...]
  }

GET /api/v1/admin/usage/monthly
  Response: { "2026-01": 12400, "2026-02": 8900, ... }
```

---

## Pipeline-integration (licensklient)

Ny modul i Proc-Product-Photos: `process_images/license.py`

```python
class LicenseClient:
    """Kommunicerar med licensservern för kvotkontroll."""

    def __init__(self, api_key: str, server_url: str):
        self.api_key = api_key
        self.server_url = server_url
        self._cached_quota = None
        self._cache_time = 0

    def check_quota(self, image_count: int) -> QuotaResult:
        """Kontrollera om vi har kvot för N bilder."""
        # Cache i 1h för att undvika onödiga anrop
        ...

    def report_usage(self, stats: dict) -> None:
        """Rapportera efter körning."""
        ...
```

CLI-integration:
```bash
# Med licensnyckel
process-images --input ./bilder --output ./output \
  --license-key PPP-XXXX-YYYY-ZZZZ \
  --license-server https://license.dindomän.se

# Eller via miljövariabel
export PPP_LICENSE_KEY=PPP-XXXX-YYYY-ZZZZ
export PPP_LICENSE_SERVER=https://license.dindomän.se
process-images --input ./bilder --output ./output
```

### Offline-grace

Om licensservern inte nås:
1. Använd cachad kvot (max 48h gammal)
2. Logga lokal usage
3. Synka vid nästa lyckade anslutning
4. Efter 48h utan kontakt: vägra köra (mjuk lockout)

---

## Säkerhet

- API-nycklar: `PPP-` prefix + 32 tecken random (base62)
- Alla anrop över HTTPS
- Rate limiting: 100 anrop/min per API-nyckel
- Brute force-skydd: lockout efter 10 felaktiga nycklar från samma IP
- Admin-endpoints kräver JWT med admin-roll
- Usage-data krypteras inte (innehåller inga bilder/filnamn)

---

## Projektstruktur

```
Proc-Product-License/
├── pyproject.toml
├── README.md
├── Dockerfile
├── docker-compose.yml        # API + PostgreSQL
├── alembic/                  # DB-migrationer
│   ├── alembic.ini
│   └── versions/
├── license_server/
│   ├── __init__.py
│   ├── main.py               # FastAPI app
│   ├── config.py             # Settings (env vars)
│   ├── database.py           # SQLAlchemy + connection
│   ├── models.py             # ORM-modeller
│   ├── schemas.py            # Pydantic request/response
│   ├── auth.py               # API-nyckel validering, JWT
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── license.py        # /license/*
│   │   ├── usage.py          # /usage/*
│   │   ├── customers.py      # /customers/*
│   │   ├── plans.py          # /plans/*
│   │   └── admin.py          # /admin/*
│   └── services/
│       ├── __init__.py
│       ├── quota.py           # Kvotberäkning med rollover
│       ├── billing.py         # Fakturagenerering
│       └── api_keys.py        # Generering + validering
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_license.py
│   ├── test_usage.py
│   ├── test_quota.py
│   └── test_billing.py
└── scripts/
    ├── create_admin.py
    ├── seed_plans.py
    └── generate_api_key.py
```

---

## Implementationsordning

### Sprint 1: Kärna (15-20h)
- FastAPI skeleton + PostgreSQL + Docker Compose
- Datamodell + Alembic-migrationer
- API-nyckel generering + validering
- /license/validate + /usage/quota + /usage/report
- Pipeline-klient (license.py) i Proc-Product-Photos
- Grundläggande tester

### Sprint 2: Kundhantering (10-15h)
- CRUD för customers, licenses, plans
- Admin-dashboard endpoint
- seed_plans.py med Starter/Business/Enterprise
- Kvotberäkning med årsrollover

### Sprint 3: Fakturering (10-15h)
- Automatisk månadsfaktura-generering
- Overage-beräkning
- Faktura-export (PDF eller CSV)
- Stripe-integration (optional, kan vara manuell fakturering först)

---

## Konfiguration (miljövariabler)

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/ppp_license
SECRET_KEY=random-secret-for-jwt
API_KEY_PREFIX=PPP
CORS_ORIGINS=https://portal.dindomän.se
RATE_LIMIT_PER_MINUTE=100
OFFLINE_GRACE_HOURS=48
```
