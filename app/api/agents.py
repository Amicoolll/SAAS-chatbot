
from fastapi import APIRouter
from app.agents.prompts import list_agents

router = APIRouter(tags=["Agents"])

@router.get("/agents")
def agents():
    return {"agents": list_agents()}