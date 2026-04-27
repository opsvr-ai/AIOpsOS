from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user
from src.models.cron_job import CronJob
from src.schemas.cron import CronJobCreate, CronJobOut, CronJobUpdate
from src.services.cron_scheduler import compute_next_run

router = APIRouter()


@router.get("/cron/jobs", response_model=list[CronJobOut])
async def list_cron_jobs(db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(CronJob).order_by(CronJob.created_at.desc()))
    return result.scalars().all()


@router.post("/cron/jobs", response_model=CronJobOut)
async def create_cron_job(body: CronJobCreate, db: DbSession, _=Depends(get_current_user)):
    job = CronJob(**body.model_dump())
    job.next_run = compute_next_run(job.schedule)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/cron/jobs/{job_id}", response_model=CronJobOut)
async def get_cron_job(job_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    return job


@router.patch("/cron/jobs/{job_id}", response_model=CronJobOut)
async def update_cron_job(job_id: str, body: CronJobUpdate, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(job, key, val)
    if body.schedule is not None or body.enabled is not None:
        job.next_run = compute_next_run(job.schedule) if job.enabled else None
    await db.commit()
    await db.refresh(job)
    return job


@router.delete("/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    await db.delete(job)
    await db.commit()
    return {"detail": "deleted"}


@router.post("/cron/jobs/{job_id}/trigger")
async def trigger_cron_job(job_id: str, db: DbSession, _=Depends(get_current_user)):
    from datetime import datetime, timezone

    result = await db.execute(select(CronJob).where(CronJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    job.next_run = datetime.now(timezone.utc)
    job.enabled = True
    await db.commit()
    return {"detail": "triggered", "job_id": job_id}
