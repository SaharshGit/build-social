# BuildSocial frontend

React + TypeScript app (built with Vite) for the BuildSocial API: review queue, post review, new posts, brands.

```bash
npm install
npm run dev        # http://localhost:5173  (needs the API on :8000: `uv run buildsocial api` in the repo root)
npm run build      # typecheck + production build into dist/
npm run gen:api    # regenerate src/api/schema.ts after the backend's API changes
```

In development, the Vite server forwards `/api/*` to FastAPI on `127.0.0.1:8000` (see `vite.config.ts`),
so the browser only ever talks to one origin and no CORS setup is needed.

- `src/api/`: `client.ts` (the only code that talks HTTP), `schema.ts` (generated types), `hooks.ts` (data hooks)
- `src/pages/`: one file per screen; `src/components/`: shared pieces
- `src/styles.css`: the one stylesheet (light/dark follow the system)
