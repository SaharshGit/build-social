# BuildSocial

AI agents that research, plan, write and review social media posts for a brand, with a person approving every post before it's used.

```
brand + brief
  ─► research (web search: shared market research + private topic research, cached)
  ─► strategy (one angle, a key message, which researched facts to use)
  ─► content  (platform-native post; platform rules checked in code)
  ─► reviewer (brand rules, invented claims, promised results) ─► one automatic revision
  ─► review queue: a person approves, rejects, or asks for a revision
```

It runs as a **web API** (FastAPI) with a **background worker**, a **Postgres** database, and a **Google Cloud Storage** bucket (or local folders) for traces and cached research. The same pipeline also runs from the command line.

## Setup

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker (Docker Desktop with WSL integration on Windows).

```bash
uv sync                      # install dependencies
uv run playwright install --only-shell chromium   # headless browser that renders post images (~280 MB, once)
cp .env.example .env         # then fill in the values (see below)
docker compose up -d         # start Postgres (127.0.0.1:5432, data kept in a Docker volume)
uv run alembic upgrade head  # create / update the database tables
uv run buildsocial import-brands examples/*.yaml   # load the example brands
```

`.env` (never committed) holds:

| Variable | What |
|---|---|
| `SARVAM_API_KEY` | strategy, content and reviewer models (Sarvam; the reviewer is DeepSeek served by Sarvam) |
| `GEMINI_API_KEY` | research (Gemini with Google Search) |
| `POSTGRES_PASSWORD`, `DATABASE_URL` | the local database; only `DATABASE_URL` changes for a cloud database |
| `BUILDSOCIAL_STORAGE` | `local` (folders in this project) or `gcs` (a bucket) for traces, research cache and saved files |
| `BUILDSOCIAL_GCS_BUCKET`, `GCS_CREDENTIALS_JSON` | for `gcs`: the bucket name and a service-account key (one line) with access to that bucket only |

Which model each agent uses, with prices, is set in [`models.yaml`](models.yaml).

## Run the API

Two processes, in two terminals:

```bash
uv run buildsocial api       # the web API: http://127.0.0.1:8000/docs
uv run buildsocial worker    # does the slow work: processes queued runs
```

Then, on the `/docs` page (or with any HTTP client):

1. `GET /brands`: pick a brand id
2. `POST /brands/{brand_id}/runs` with `{"topic": "...", "platform": "instagram"}`: returns a run id immediately (status `queued`)
3. `GET /runs/{run_id}`: watch `current_step` and `steps` until `status` is `done`; it then has a `post_id`
4. `GET /posts?status=pending`: the review queue; `GET /posts/{post_id}` shows the post, its open review issues, its plan, and the facts it used with their sources
5. `POST /posts/{post_id}/approve`, `/reject`, or `/revise` with `{"feedback": "..."}` (a revision updates the same post; earlier drafts stay in its history)

There is no login yet: everything belongs to one default organization.

## Command line

```bash
uv run buildsocial run --brand examples/mamaearth.yaml --topic "onion hair oil for winter hair care" --platform instagram
uv run buildsocial research ...     # research only
uv run buildsocial trace            # what happened in the last run (prompts, replies, cost)
uv run buildsocial spend            # recorded LLM cost per model
uv run buildsocial eval --label my-change          # score 6 fixed cases (frozen research)
uv run buildsocial eval-compare <a.json> <b.json>  # compare two eval runs
```

## Tests

```bash
uv run pytest
```

All tests use fake models and a throwaway in-memory database: no API keys, no Docker, no cost.

## Costs

A typical run costs about ₹0.12–0.19 of Sarvam credit. Research calls Gemini only when it isn't cached: market research is shared per industry and market for a week, topic research is cached per brand for 7 days. `buildsocial spend` shows the recorded totals.

## Project layout

```
buildsocial/
├── harness/     generic agent harness: LLM adapters (Sarvam, Gemini, fallback), agent loop, tracing, storage, skills
├── agents/      research, strategy, content, reviewer, judge (+ prompts/)
├── skills/      per-platform and per-format writing guidance
├── pipeline.py  the agents in order
├── api/         FastAPI app and routes
├── db/          database tables and operations
├── worker.py    background worker
└── cli.py       the `buildsocial` command
migrations/      database schema changes (Alembic)
examples/        test brand profiles (local test data, see below)
evals/           eval cases and frozen research
docs/            architecture and roadmap
```

## Notes

- `examples/mamaearth.yaml` and `examples/zerodha.yaml` are local test files built from public information. They aren't affiliated with those companies, and generated posts must not be published. `brew-house.yaml` is fictional.
- Never commit `.env` or service-account keys. `traces/`, `research_cache/`, `output/` and `evals/results/` are git-ignored.

See [docs/ARCHITECTURE_AND_ROADMAP.md](docs/ARCHITECTURE_AND_ROADMAP.md) for the design and what's next.
