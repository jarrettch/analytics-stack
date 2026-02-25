from fastapi import APIRouter

router = APIRouter(prefix="/api/dodgers", tags=["Dodgermetrics"])

@router.get("/roster")
async def get_roster():
    return {"status": "success", "data": "roster data goes here"}