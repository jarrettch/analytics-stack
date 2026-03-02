import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import ev, dodgers, nba

app = FastAPI(title="Analytics Stack API")

# Use the Env Var suggestion from your notes
origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3001,http://localhost:3002,https://analytics-stack-visualize-nyyuclcon-jarrettchs-projects.vercel.app,https://www.visualize-ev.gg,https://analytics-stack-visualize-ev.vercel.app").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the routers
app.include_router(ev.router)
app.include_router(dodgers.router)
app.include_router(nba.router)

@app.get("/health")
def health_check():
    return {"status": "healthy"}