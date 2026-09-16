from fastapi import APIRouter

from manager_backend.models.schemas import AppVersionResponse
from manager_backend.services.version_service import get_policy

router = APIRouter(tags=["App"])


@router.get("/app/version", response_model=AppVersionResponse)
async def app_version() -> AppVersionResponse:
    policy = get_policy()
    return AppVersionResponse(
        current_version=policy.current_version,
        minimum_version=policy.minimum_version,
        download_url=policy.download_url,
    )
