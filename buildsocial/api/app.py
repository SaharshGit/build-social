"""The BuildSocial API (FastAPI).

Run:   uv run buildsocial api            (then open http://127.0.0.1:8000/docs)
The API only reads and writes the database; slow work (pipeline runs) will be done by a
separate worker process (`buildsocial worker`), so every request returns quickly.
"""

from fastapi import FastAPI

from buildsocial.api.routes import brands, conversations, health, posts, runs


def create_app() -> FastAPI:
    app = FastAPI(
        title="BuildSocial API",
        version="0.1.0",
        description="Brands, pipeline runs and the post review queue. No login yet: everything belongs "
        "to one default organization.",
    )
    app.include_router(health.router)
    app.include_router(brands.router)
    app.include_router(runs.router)
    app.include_router(posts.router)
    app.include_router(conversations.router)
    return app


app = create_app()
