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
- Next: prototype 1 (Research → Strategy → Content → approval, end to end).
- Adding a platform or format: add it to `schemas.py`, add its skill file, and (platforms) its limits in `PLATFORM_RULES`; a test fails if any is missing.
- Open: the model invents facts and can break brand rules subtly; needs the approval step and an LLM judge in evals.
