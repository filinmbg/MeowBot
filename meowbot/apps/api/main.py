from __future__ import annotations

from fastapi import FastAPI

from meowbot.apps.api.routes.settings import router as settings_router
from meowbot.apps.api.routes.stats import router as stats_router
from meowbot.apps.api.routes.telegram_auth import router as telegram_auth_router
from meowbot.apps.api.routes.users import router as users_router
from meowbot.infra.postgres.client import get_pg_pool
from meowbot.infra.mongo.client import MongoConn, MongoConfig


app = FastAPI(title="MeowBot API")


@app.on_event("startup")
async def startup():
    app.state.pg = await get_pg_pool()

    mongo = MongoConn(MongoConfig())
    mongo.connect()
    app.state.mongo = mongo

    print("✅ API started")
    print("✅ Postgres connected")
    print("✅ Mongo connected")


@app.on_event("shutdown")
async def shutdown():
    if hasattr(app.state, "pg"):
        await app.state.pg.close()
    if hasattr(app.state, "mongo"):
        app.state.mongo.close()


@app.get("/health")
async def health():
    pg_ok = True
    mongo_ok = True

    try:
        async with app.state.pg.acquire() as conn:
            await conn.fetchval("SELECT 1;")
    except Exception:
        pg_ok = False

    try:
        mongo_ok = app.state.mongo.ping()
    except Exception:
        mongo_ok = False

    overall = pg_ok and mongo_ok

    return {
        "status": "ok" if overall else "degraded",
        "services": {
            "api": True,
            "postgres": pg_ok,
            "mongo": mongo_ok,
        },
    }


app.include_router(users_router)
app.include_router(telegram_auth_router)
app.include_router(settings_router)
app.include_router(stats_router)