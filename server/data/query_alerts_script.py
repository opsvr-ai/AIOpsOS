import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

db_url = 'postgresql+asyncpg://aiopsos:aiopsos123@localhost:5432/aiopsos'

async def query():
    engine = create_async_engine(db_url)
    async with engine.connect() as conn:
        # Get active alerts (non-closed, non-dismissed)
        result = await conn.execute(
            text("SELECT id, title, source, severity, status, created_at, assigned_to "
                 "FROM alerts WHERE status NOT IN ('closed', 'dismissed') "
                 "ORDER BY created_at DESC")
        )
        rows = result.fetchall()
        report = []
        report.append(f'Active alerts count: {len(rows)}')
        for row in rows:
            report.append(f'  ID={row[0]}, Title={row[1]}, Source={row[2]}, Severity={row[3]}, Status={row[4]}, Created={row[5]}, Assigned={row[6]}')

        result2 = await conn.execute(
            text("SELECT status, severity, COUNT(*) as cnt "
                 "FROM alerts GROUP BY status, severity ORDER BY status, severity")
        )
        rows2 = result2.fetchall()
        report.append('\nAlert counts by status and severity:')
        for row in rows2:
            report.append(f'  Status={row[0]}, Severity={row[1]}, Count={row[2]}')

        # Get cron jobs
        try:
            cr = await conn.execute(
                text("SELECT id, name, schedule, enabled, last_run, next_run, created_at "
                     "FROM cron_jobs ORDER BY created_at")
            )
            cron_rows = cr.fetchall()
            report.append(f'\nCron jobs count: {len(cron_rows)}')
            for row in cron_rows:
                report.append(f'  ID={row[0]}, Name={row[1]}, Schedule={row[2]}, Enabled={row[3]}, LastRun={row[4]}, NextRun={row[5]}, Created={row[6]}')
        except Exception as e:
            report.append(f'\nCron jobs query error: {e}')

    await engine.dispose()
    print('\n'.join(report))

asyncio.run(query())
