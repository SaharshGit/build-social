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
- Next: verify GCS against the real `buildsocial` bucket once the service-account key is in .env.
- Adding a platform or format: add it to `schemas.py`, add its skill file, and (platforms) its limits in `PLATFORM_RULES`; a test fails if any is missing.
- Open: research facts can be safe but thin (strategy picks few facts); reviewer sometimes over-flags; Sarvam hangs.
