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
│   │   │   ├── gemini.py         # adapter: our types <-> Gemini Interactions API (+ Google Search)
│   │   │   ├── _http.py          # shared: HTTP POST with retries
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
- **One adapter per provider, written for that provider's real behaviour** (not a "generic" adapter guessing at many). Now: `sarvam.py` (writing agents) and `gemini.py` (research, with Google Search). Later, as needed: `openai_responses.py`, `anthropic.py`, etc. Shared HTTP retry logic lives in `_http.py`. Different models from the same provider share one adapter; their differences are settings in `models.yaml`.
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
- [x] Step 2: Content agent with no tools: BrandProfile → structured PostDraft, via CLI (`uv run buildsocial content ...`). Reasoning off by default (~₹0.035 and ~3s per post, vs ~₹0.3–0.5 and 35–60s with reasoning `low`)
- [x] Step 2f: Skills files (`buildsocial/skills/platforms/*.md`, `skills/formats/text.md`) loaded per brief by `harness/skills.py`; `format` added to the brief (only `text` implemented)
- [x] Step 3: Tracing: every run writes a JSON Lines trace to `traces/` (prompts, replies, checks, tokens, cost); view with `uv run buildsocial trace`
- [ ] Step 4: Tools + agent loop: Research agent with web search
- [ ] Step 5: Pipeline: Research → Strategy → Content
- [ ] Step 6: Human approval in CLI (approve / edit / reject with feedback)
- [ ] Step 7: Evals: example brands + output quality checks
- [ ] Step 8: Decide: keep own harness vs adopt LangGraph; then wrap in FastAPI

## 7. Content formats and visuals (planned, not built)

**Long-term goal:** produce any kind of social media content: text posts, graphics, carousels, video, animation, audio.

**Two stages for every format:**
- *What to say*: agents produce a structured **creative spec** (today's `PostDraft` is the spec for a text post).
- *How it looks or sounds*: a production step turns the spec into files (assets).

**Scope now:** text posts, then graphics for text content (quote cards, offer cards, carousels).
**Out of scope for now:** image generation, voice, video. Sarvam has no image or video generation; it does offer TTS (Bulbul), STT (Saaras), translation, dubbing and image-input models.

**Graphics approach (code-based design):**
- A Design agent writes HTML/CSS for each graphic, constrained by a **brand kit** in the brand profile (colours, fonts, logo, style notes), with example layouts supplied as skills files.
- A headless browser (Playwright) renders it to PNG, with scripts and network access disabled.
- Same validate-and-retry loop as text: code checks (overflow, size, contrast, brand colours/fonts), then a vision-model check; problems go back to the agent.
- Fallback: a fixed template if the design still fails after retries.
- Optional later: a Canva renderer for users with their own Canva brand templates (Connect API; autofill may require Canva Enterprise; each user connects their own account).

**Code-based creative tools in general:** the same pattern (agent writes code → a renderer produces media → checks → retry) covers other tools later, e.g. Manim for animated explainer videos, SVG, Remotion for motion graphics. They plug in as tools, either our own Python tools or MCP servers. Tools that execute LLM-written programs (Manim runs Python) must run in a sandbox (an isolated container with no network or secrets).

**Skills files (Step 2f):** per-platform and per-format know-how in Markdown (`buildsocial/skills/platforms/instagram.md`, `skills/formats/carousel.md`), loaded by code based on the brief so each call carries only the guidance it needs.

**Other capabilities use the same adapter pattern as `llm/`,** each added only when an agent needs it: `search/` (Step 4), later `images/`, `speech/`, `video/`. The tools layer (Step 4) should accept both Python tools and tools from MCP servers.

**Known quality gaps (from Step 2 runs):** the model invents plausible facts (addresses, durations) and can break brand rules subtly (health claims). Code rules can't catch these; they need the human approval step (Step 6) and an LLM judge in evals (Step 7).

## 8. Later (not now)

FastAPI service, Postgres (drafts, runs, brands), background jobs, React UI, OAuth social connections and publishing, multi-tenancy, Docker, deployment.
