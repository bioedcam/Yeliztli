# Yeliztli

Personal genomics analysis platform for 23andMe raw data. Upload your 23andMe file, annotate variants against clinical databases, and explore findings across 15 specialized analysis modules — all running locally on your machine.

**Your data never leaves your computer.** Yeliztli runs entirely on localhost with no telemetry, no cloud processing, and no outbound variant data.

## Features

- **Variant annotation** against ClinVar, gnomAD, dbNSFP, VEP, and ENCODE
- **15 analysis modules**: Pharmacogenomics, Nutrigenomics, Cancer, Cardiovascular, APOE, Carrier Status, Ancestry, Fitness, Sleep, Skin, Methylation, Allergy, Traits & Personality, Gene Health, Rare Variants
- **Interactive variant explorer** with cursor-based pagination, column presets, and advanced filtering
- **Genome browser** (IGV.js) for visual variant inspection
- **Custom query builder** with SQL console for advanced analysis
- **PDF report generation** with clinical-grade typography
- **Export** to VCF, TSV, JSON, CSV, and FHIR R4 DiagnosticReport
- **Dark mode** with blue/teal medical theme
- **Optional authentication** (PIN/password with session timeout)
- **Automatic database updates** with configurable schedule
- **WCAG 2.1 AA accessible** — keyboard navigation, screen reader support, axe-core tested

## Quick Start (Development)

```bash
git clone https://github.com/bioedcam/GenomeInsight.git
cd GenomeInsight
pip install -e ".[dev]"
cd frontend && npm install && cd ..
make dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser. The setup wizard will guide you through first-time configuration.

For detailed installation options (native services, Docker, WSL2), see the [Setup Guide](docs/setup-guide.md).

For a walkthrough of all features, see the [Usage Guide](docs/usage-guide.md).

## Requirements

- Python 3.12+
- Node 20+ (for frontend)
- ~2 GB disk space (application + reference databases)
- Java 8+ (optional, for chromosome-level ancestry painting via Beagle phasing)

### Ancestry Module

Yeliztli includes a two-tier ancestry analysis system:

- **Tier 1 (Instant):** 5,000-AIM PCA projection with NNLS admixture estimation across 7 superpopulations (AFR, AMR, CSA, EAS, EUR, MID, OCE). Runs in under 1 second. Always available.
- **Tier 2 (Deep Analysis):** Local ancestry inference via Gnomix models with Beagle phasing. Provides chromosome-level ancestry painting. Runs in 15–30 minutes. Requires an optional LAI bundle download (~500 MB) and Java 8+.

The LAI bundle can be downloaded during initial setup or later from the Ancestry page. See [Ancestry Module Documentation](docs/ANCESTRY_MODULE.md) for methods and validation details.

## Development

```bash
make dev               # Start backend (port 8000) + frontend (port 5173)
make test              # Run all tests (backend + frontend)
make test-e2e          # Run Playwright E2E tests
make lint              # Lint with Ruff
make format            # Auto-format with Ruff
make benchmark         # Run annotation pipeline benchmark (600k SNPs)
```

**Backend only:** `make run-api`
**Frontend only:** `make run-frontend`
**Huey worker:** `make run-huey`

### Project Structure

```text
GenomeInsight/
├── backend/                 # FastAPI application
│   ├── analysis/            # 15+ analysis modules
│   ├── annotation/          # Variant annotation engine
│   ├── api/routes/          # API endpoints
│   ├── db/                  # SQLite schema & connections
│   ├── ingestion/           # 23andMe parser
│   ├── reports/             # PDF report templates
│   └── config.py            # Pydantic Settings configuration
├── frontend/                # React 18 + TypeScript SPA
│   └── src/
│       ├── components/      # Reusable UI components
│       ├── pages/           # Route pages
│       └── hooks/           # React Query hooks
├── tests/                   # Backend tests (pytest)
├── alembic/                 # Database migrations
├── scripts/                 # Build & utility scripts
├── systemd/                 # Linux/WSL2 service units
├── launchd/                 # macOS service plists
├── docs/                    # Documentation
│   ├── setup-guide.md       # Installation & deployment
│   └── usage-guide.md       # Feature walkthrough
├── Dockerfile               # Container image
├── docker-compose.yml       # Multi-service deployment
├── Makefile                 # Dev shortcuts
└── pyproject.toml           # Python project config
```

### Configuration

Yeliztli uses layered configuration (highest priority first):

1. Environment variables (`YELIZTLI_` prefix)
2. `~/.yeliztli/config.toml`
3. `.env` file
4. Built-in defaults

See [`backend/config.py`](backend/config.py) for all available settings.

### Running Tests

```bash
make test              # All tests (backend + frontend)
make test-backend      # Backend only (pytest)
make test-frontend     # Frontend only (Vitest)
make test-e2e          # Playwright E2E (Chrome, Firefox, WebKit)
```

## Module Status

| Module | Backend | Frontend | Phase |
|--------|---------|----------|-------|
| Setup Wizard | Complete | Complete | 1 |
| Dashboard | Complete | Complete | 1 |
| Variant Explorer | Complete | Complete | 2 |
| Variant Detail | Complete | Complete | 2 |
| Genome Browser (IGV.js) | Complete | Complete | 2 |
| Command Palette | — | Complete | 4 |
| Pharmacogenomics | Complete | Complete | 3 |
| Nutrigenomics | Complete | Complete | 3 |
| Cancer | Complete | Complete | 3 |
| Cardiovascular | Complete | Complete | 3 |
| APOE | Complete | Complete | 3 |
| Carrier Status | Complete | Complete | 3 |
| Ancestry | Complete | Complete | 3 |
| Fitness | Complete | Complete | 3 |
| Sleep | Complete | Complete | 3 |
| Skin | Complete | Complete | 3 |
| Methylation | Complete | Complete | 3 |
| Allergy | Complete | Complete | 3 |
| Traits & Personality | Complete | Complete | 3 |
| Gene Health | Complete | Complete | 3 |
| Rare Variants | Complete | Complete | 3 |
| Query Builder | Complete | Complete | 4 |
| Reports | Complete | Complete | 4 |
| Settings & Admin | Complete | Complete | 4 |
| Authentication | Complete | Complete | 4 |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ImportError: cannot import name 'UTC'` | Python < 3.12 | Install Python 3.12+ |
| `ModuleNotFoundError: No module named 'backend'` | Package not installed | `pip install -e ".[dev]"` |
| Node version errors during `npm install` | Node < 20 | Install Node 20+ (`nvm install 20`) |
| `database is locked` / SQLite WAL errors | Concurrent writes without WAL | Default config uses WAL — check `wal_mode = true` in config.toml |
| Annotation pipeline hangs | Huey worker not running | Start with `make run-huey` or `make dev` |
| Blank page at localhost:5173 | Backend not running | Start with `make dev` (runs both servers) |
| Reference DB download fails | Network issue | Re-run from Settings > Database Management; downloads are resumable |

## Data Sources & Attribution

Yeliztli annotates variants against several public reference datasets. Most
are downloaded and built locally; **gnomAD** ships as a prebuilt, redistributable
allele-frequency bundle.

- **gnomAD (Genome Aggregation Database)** — population allele frequencies.
  <https://gnomad.broadinstitute.org/> · CC0 1.0 (public domain dedication).
  Bundle scope: allele frequencies and homozygous counts only (no academic-license
  predictor columns). Cite: Karczewski, K.J., Francioli, L.C., Tiao, G. et al.,
  "The mutational constraint spectrum quantified from variation in 141,456 humans,"
  *Nature* 581, 434–443 (2020), doi:10.1038/s41586-020-2308-7.
  "gnomAD" and "Broad Institute" are trademarks of their respective owners, used
  here solely for source attribution; Yeliztli is an independent project and
  is not affiliated with or endorsed by the Broad Institute.

See the repo-root [`NOTICE`](NOTICE) file for the full third-party data
attribution list.

## License

MIT
