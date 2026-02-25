from fastapi import APIRouter

router = APIRouter(prefix="/api/ev", tags=["Expected Value"])

@router.get("/scenarios")
async def get_ev_scenarios():
    # This is where your VEV logic will live
    return {"game": "Poker", "scenario": "Flush Draw", "ev": 1.25}