import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import mlb, nba
from routers.utils import close_http_client, get_http_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_http_client()  # warm the pool so the first request doesn't pay connection cost
    yield
    await close_http_client()


app = FastAPI(title="Analytics Stack API", lifespan=lifespan)

# Use the Env Var suggestion from your notes
origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3001,http://localhost:3002,https://analytics-stack-visualize-nyyuclcon-jarrettchs-projects.vercel.app,https://www.visualize-ev.gg,https://analytics-stack-visualize-ev.vercel.app").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(nba.router)
app.include_router(mlb.router)


@app.get("/health")
def health_check():
    return {"status": "healthy"}