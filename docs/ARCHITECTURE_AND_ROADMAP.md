# BuildSocial: Agent & Harness Architecture

## 1. Scope (current phase)

The current phase covers only the **agent layer**: a small, model-agnostic harness plus the BuildSocial agents, run from the command line.

Not in scope yet: web API (FastAPI), database, frontend, Docker, deployment. These wrap the agent layer later and don't change its design.

## 2. Key concepts

- **Agent**: an LLM + system prompt + tools + typed output, run in a loop. The loop calls the LLM; if it requests a tool, the harness runs the tool, feeds the result back, and repeats until the agent returns a final answer that matches its output schema.
- **Harness**: reusable, domain-agnostic code around agents. It runs the loop, calls LLMs through a provider-neutral interface, executes tools, validates outputs, and traces every step with token/cost usage.
- **Pipeline**: plain code (not an LLM) that runs agents in a fixed order, passing one agent's validated output to the next as input, with a human approval step.

## 3. Repository structure

```
build-social/
├── pyproject.toml          # project + dependencies (uv)
├── .env.example            # API keys only
├── docs/
├── buildsocial/
│   ├── harness/            # GENERIC: no social-media knowledge
│   │   ├── llm/
│   │   │   ├── types.py          # Message, ToolSpec, ToolCall, LLMResponse, Usage
│   │   │   ├── base.py           # LLMClient interface
│   │   │   ├── sarvam.py         # adapter: our types <-> Sarvam API (one adapter per provider)
│   │   │   └── registry.py       # reads models.yaml → builds the right client for a model alias
│   │   ├── agent.py        # Agent definition + run loop
│   │   ├── tools.py        # Python function → tool the LLM can call
│   │   ├── trace.py        # per-run log of steps, tokens, cost
│   │   └── pipeline.py     # sequential agents + human approval
│   ├── agents/             # DOMAIN: research.py, strategy.py, content.py
│   ├── tools/              # domain tools: web_search, fetch_url, ...
│   ├── schemas.py          # shared types: BrandProfile, PostDraft, ...
│   └── cli.py              # command-line entry point
├── examples/               # sample brand profiles
├── evals/                  # quality test cases for agent outputs
├── tests/                  # unit tests
└── traces/                 # run logs (gitignored)
```

The `harness/` vs `agents/` split keeps the harness reusable for future agent suites beyond social media.

## 4. Model-agnostic LLM layer

**Principle: design the interface for any provider, implement only what we use.**

- **Adapter pattern.** Agents and the harness never see provider JSON or SDKs. They use only our types (`buildsocial/harness/llm/types.py`). Each API format gets one adapter that translates both ways:
  - outgoing: our `Message` / `ToolSpec` → provider request JSON
  - incoming: provider response → our `LLMResponse` / `Usage`
- `base.py` defines the `LLMClient` contract every adapter implements: `complete(messages, tools, ...) -> LLMResponse`.
- **One adapter per provider, written for that provider's real behaviour** (not a "generic" adapter guessing at many). Now: `sarvam.py` only. Later, as needed: `openai_responses.py`, `anthropic.py`, `gemini.py`, etc. Different models from the same provider share one adapter; their differences are settings in `models.yaml`.
- If two adapters end up sharing a lot of code (e.g. two OpenAI-format providers), extract the shared helpers into a common module *then*, once the overlap is visible, not before.
- **Usage normalization.** Adapters convert token counts into non-overlapping buckets (`input_tokens`, `cache_read_tokens`, `cache_write_tokens`, `output_tokens`, with `reasoning_tokens` as a subset of output). The provider's original usage is kept in `raw_usage` for auditing.
- Provider quirks live only inside the adapter. Only Sarvam's are handled for now (e.g. it rejects `tool_choice` without `tools`).
- Structured output: the prompt asks for JSON, Pydantic validates it, and invalid output triggers a retry that includes the error.
- **Config split:**
  - `.env` holds secrets only: one API key per provider (e.g. `SARVAM_API_KEY`).
  - `models.yaml` (repo root) holds everything else: model aliases (base URL, model name, `api_key_env`, prices) and which alias each agent uses, so different steps can use different models.
  - `registry.py` is a factory: `get_client("research")` looks up the agent's alias, builds the adapter, and injects the key. Agents never construct clients themselves.
- Check model names against Sarvam's docs; they change.
- Each model in `models.yaml` has a `provider` field (e.g. `sarvam`) telling the registry which adapter to build.
- **Later:** a passthrough field on `Message` for provider reasoning state (needed by some native adapters for multi-turn tool use).

## 5. Agent pipeline (v1)

```
BrandProfile ─► Research Agent (web_search, fetch_url) ─► ResearchReport
             ─► Strategy Agent ─► ContentPlan
             ─► Content Agent  ─► PostDraft
             ─► Human approval (approve / edit / reject + feedback → revise)
             ─► saved draft
```

Later agents: Creative, Publishing, Analytics.

### Design rules
1. Every agent has typed input and output (Pydantic). Agents never talk to each other directly.
2. Pipeline order is plain code, not LLM-decided: predictable, debuggable, cheap.
3. The harness enforces schema validation with retries, a max-steps limit, and per-run token/cost limits.
4. Every run writes a trace: inputs, LLM calls, tool calls, outputs, tokens, cost.
5. Rejection feedback is fed back to the Content agent for revision (CLI first, a UI approve button later).
6. Deterministic Python for anything that must be exact (API calls, calculations); LLMs for reasoning and writing.

## 6. Roadmap

- [x] Step 0: Clean repo structure
- [x] Step 1: LLM layer (`types`, `base`, `sarvam`, `registry` + `models.yaml`) + smoke test (`uv run python -m buildsocial.harness.llm`)
- [ ] Step 2: Content agent with no tools: BrandProfile → structured PostDraft, via CLI
- [ ] Step 3: Tracing: tokens, cost, step log per run
- [ ] Step 4: Tools + agent loop: Research agent with web search
- [ ] Step 5: Pipeline: Research → Strategy → Content
- [ ] Step 6: Human approval in CLI (approve / edit / reject with feedback)
- [ ] Step 7: Evals: example brands + output quality checks
- [ ] Step 8: Decide: keep own harness vs adopt LangGraph; then wrap in FastAPI

## 7. Later (not now)

FastAPI service, Postgres (drafts, runs, brands), background jobs, React UI, OAuth social connections and publishing, multi-tenancy, Docker, deployment.
