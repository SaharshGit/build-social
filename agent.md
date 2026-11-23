# Workspace Instructions: BuildSocial

## 1. Context
- Read `docs/ARCHITECTURE_AND_ROADMAP.md` before proposing changes or creating modules.
- BuildSocial is meant to be a real product: a multi-agent suite that researches, plans and writes social media content with human approval.
- **Current phase: agent + harness only**, run from the CLI. No web API, database, frontend or Docker yet.

## 2. Architectural boundaries
- `buildsocial/harness/` is generic and must not contain social-media logic. `buildsocial/agents/` and `buildsocial/tools/` hold the domain code.
- Agents and the harness never import a provider SDK; they use `buildsocial/harness/llm/types.py` and the `LLMClient` interface. Provider-specific code lives only in adapters.
- Model names, endpoints, prices and per-agent model choice live in `models.yaml`; API keys live in `.env`. Never hardcode either.
- Agents exchange typed Pydantic models through the pipeline; they never talk to each other directly.
- Use deterministic Python for exact work and LLMs for reasoning and generation.

## 3. Collaboration style
- **Plan before building.** Don't write code until the user approves the step.
- **One step at a time.** Before writing each file, explain why it exists, how it fits in, and the tradeoffs. Verify each step runs before moving on.
- **Minimal dependencies.** Add a dependency only when a step needs it, and say why.
- **Cost-conscious.** Track tokens and cost per run from early on.

## 4. Current status
- Done: Step 0 (repo structure cleaned), Step 1 (LLM layer + real Sarvam call working).
- Done: Step 2 (Content agent via CLI, platform rules in code, cost reporting, reasoning off by default).
- Done: Step 2f (skills files per platform and format, loaded by code from the brief).
- Done: Step 3 (tracing to `traces/`, viewer: `uv run buildsocial trace`).
- Done: Gemini adapter (`harness/llm/gemini.py`, Interactions API, Google Search built in); research model chosen in models.yaml.
- Done: Two-layer research (shared market + private topic) with caching in `research_cache/`.
- Done: Strategy, Content, Reviewer (+1 automatic revision), human approval loop, posts saved to `output/`.
- Done: Product facts in brand profiles; findings tagged by scope; `buildsocial spend` for recorded cost.
- Models: research `gemini-3.5-flash-lite` (paid, small credit: only on cache miss); strategy/content `sarvam-105b`
  (20s timeout: about half its calls hang, retries answer fast); reviewer `deepseekv4.1-flash-1017` via Sarvam.
- Test brands (local fixtures only): `examples/brew-house.yaml` (fictional), `mamaearth.yaml`, `zerodha.yaml`.
- Done: evals (`buildsocial eval`, `eval-compare`): 6 cases, frozen research, code checks + DeepSeek judge.
  Results 2026-11-18: baseline 3/6 -> 6/6 after product facts + must-name-product check (repeat run also 6/6).
  Average scores move ~0.3 between identical runs: don't trust smaller differences from a single run.
  2 revision rounds: fewer open issues (13 -> 7) but worse safety (3/6); default stays 1 (`--max-revisions`).
- Done: fallback model (sarvam-105b -> deepseekv4.1-flash-1017 on transient failures).
- Done: storage layer (`harness/storage.py`): ALL runtime data (traces, research cache, posts, eval results)
  goes through Storage: local (default) or a GCS bucket (`BUILDSOCIAL_STORAGE=gcs`). Project files stay files.
- Done: runtime data on GCS bucket `gs://build-social` (BUILDSOCIAL_STORAGE=gcs; key as one-line GCS_CREDENTIALS_JSON
  in .env, service account with Storage Object Admin on that bucket only). Local research cache, posts and eval
  results copied up; old local traces still only on the laptop.
- API phase (branch `sg/api`): Step 1 done: Postgres in Docker (`docker compose up -d`, 127.0.0.1 only),
  tables organizations/brands/runs/posts (`buildsocial/db/models.py`), Alembic migrations in `migrations/`
  (`uv run alembic upgrade head`). DATABASE_URL in .env; only it changes for Cloud SQL later.
  Step 2 done: FastAPI app (`buildsocial/api/`), GET /health, brand CRUD at /brands (body = BrandProfile),
  `buildsocial api` (http://127.0.0.1:8000/docs), `buildsocial import-brands examples/*.yaml`.
  No login yet: one default organization. API tests use in-memory SQLite (tests/conftest.py).
  Step 3 done: POST /brands/{id}/runs (202, queued), GET /runs/{id} (live progress), GET /runs;
  `buildsocial worker` claims runs with SELECT ... FOR UPDATE SKIP LOCKED, runs the pipeline with an
  on_progress callback, saves the post (status pending) or the error; stuck runs failed on restart.
  Verified end to end on Postgres + real models (~16s, ~INR 0.14).
  Step 4 done: review queue at /posts (list, get with full_text, approve, reject, revise -> revision run that
  updates the SAME post, old draft in history). Verified end to end on Postgres + real models.
  Found: revision feedback is only partly applied (asked <400 chars, got 633) and nothing checks it.
  Next: Step 5 (wrap-up: README for running API + worker, push/PR), then quality work.
- Frontend phase (branch `sg/frontend`): F1 done: React + Vite + TypeScript in `frontend/`, React Router,
  TanStack Query, types generated from the API (`npm run gen:api`), dev proxy /api -> :8000, sidebar layout +
  API status dot, placeholder pages. Plain CSS. WSL has only 3.8 GB RAM (builds can be killed when tight).
  Next: F2 review queue + post detail (read only).
- Adding a platform or format: add it to `schemas.py`, add its skill file, and (platforms) its limits in `PLATFORM_RULES`; a test fails if any is missing.
- Open: research facts can be safe but thin (strategy picks few facts); reviewer sometimes over-flags; Sarvam hangs.
