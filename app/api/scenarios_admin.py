"""Admin API and UI for agent scenarios. Basic Auth."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from redis.asyncio import Redis

from app.api.admin import verify_admin
from app.api.scenarios_admin_ui import get_admin_scenarios_html
from app.core.dependencies import get_redis
from app.services.scenario_router import clear_scenario_embedding_cache, rebuild_scenario_embeddings
from app.services.scenarios import (
    get_scenarios_config,
    get_scenarios_status,
    reload_scenarios,
    save_scenarios,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/scenarios/status")
async def scenarios_status(_: None = Depends(verify_admin)):
    return get_scenarios_status()


@router.get("/scenarios")
async def get_scenarios(_: None = Depends(verify_admin)):
    config = get_scenarios_config()
    return config.model_dump()


@router.put("/scenarios")
async def put_scenarios(
    body: dict,
    _: None = Depends(verify_admin),
    redis_client: Redis = Depends(get_redis),
):
    try:
        config = save_scenarios(body)
        clear_scenario_embedding_cache()
        try:
            n = await rebuild_scenario_embeddings(redis_client)
        except Exception as e:
            logger.warning("Scenario embeddings rebuild after save failed: %s", e)
            n = 0
        return {
            "status": "success",
            "updated_at": config.updated_at,
            "count": len(config.scenarios),
            "router_embeddings": n,
        }
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors()) from e
    except Exception as e:
        logger.error("Failed to save scenarios: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/scenarios/reload")
async def post_scenarios_reload(
    _: None = Depends(verify_admin),
    redis_client: Redis = Depends(get_redis),
):
    config = reload_scenarios()
    clear_scenario_embedding_cache()
    try:
        n = await rebuild_scenario_embeddings(redis_client)
    except Exception as e:
        logger.warning("Scenario embeddings rebuild after reload failed: %s", e)
        n = 0
    return {
        "status": "success",
        "message": "Scenarios reloaded",
        "count": len(config.scenarios),
        "updated_at": config.updated_at,
        "router_embeddings": n,
    }


@router.get("/admin/scenarios", response_class=HTMLResponse)
async def admin_scenarios_page(_: None = Depends(verify_admin)):
    return get_admin_scenarios_html()
