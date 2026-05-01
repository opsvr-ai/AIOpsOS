from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession
from src.models.assistant import PersonalAssistantConfig
from src.schemas.assistant import PersonalAssistantConfigOut, PersonalAssistantConfigUpdate

router = APIRouter()


async def _get_or_create_config(db: DbSession, user_id: str) -> PersonalAssistantConfig:
    result = await db.execute(
        select(PersonalAssistantConfig).where(PersonalAssistantConfig.user_id == user_id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = PersonalAssistantConfig(user_id=user_id)
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


@router.get("/assistant/config", response_model=PersonalAssistantConfigOut)
async def get_config(user: CurrentUser, db: DbSession):
    return await _get_or_create_config(db, str(user.id))


@router.put("/assistant/config", response_model=PersonalAssistantConfigOut)
async def update_config(
    body: PersonalAssistantConfigUpdate, user: CurrentUser, db: DbSession
):
    config = await _get_or_create_config(db, str(user.id))
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(config, field, value)
    await db.commit()
    await db.refresh(config)
    return config
