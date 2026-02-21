"""GET /synonyms/reload, GET /synonyms/status. ТЗ Б.7."""
from datetime import datetime

from fastapi import APIRouter
from app.utils.synonyms import load_synonyms, synonyms_dict, synonyms_last_modified, SYNONYMS_FILE_PATH
import os

router = APIRouter()


@router.get("/synonyms/reload")
async def reload_synonyms():
    from app.utils import synonyms as syn_module
    syn_module.synonyms_last_modified = 0
    load_synonyms()
    return {
        "status": "success",
        "message": "Synonyms reloaded",
        "synonyms": {
            "ru_count": len(synonyms_dict.get("ru", {})),
            "uz_count": len(synonyms_dict.get("uz", {})),
        },
    }


@router.get("/synonyms/status")
async def synonyms_status():
    return {
        "file_exists": os.path.exists(SYNONYMS_FILE_PATH),
        "file_path": SYNONYMS_FILE_PATH,
        "last_modified": datetime.fromtimestamp(synonyms_last_modified).isoformat()
        if synonyms_last_modified
        else None,
        "ru_terms": len(synonyms_dict.get("ru", {})),
        "uz_terms": len(synonyms_dict.get("uz", {})),
        "sample_ru": dict(list(synonyms_dict.get("ru", {}).items())[:3]),
        "sample_uz": dict(list(synonyms_dict.get("uz", {}).items())[:3]),
    }
