"""Seed database with default roles, permissions, admin user, tools, and agents."""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.security import hash_password
from src.models.base import async_session_factory
from src.models.user import Permission, Role, User

logger = logging.getLogger(__name__)


async def seed():
    async with async_session_factory() as db:
        # Ensure permissions exist
        resources = ["agents", "tools", "scenarios", "schedules", "triggers",
                     "alerts", "channels", "agent_profiles", "users", "roles", "admin"]
        actions = ["view", "create", "update", "delete"]

        existing_perm = await db.scalar(select(Permission.id).limit(1))
        if existing_perm is None:
            all_perms: list[Permission] = []
            for res in resources:
                for act in actions:
                    p = Permission(resource=res, action=act)
                    db.add(p)
                    all_perms.append(p)
            await db.flush()
            logger.info("Created %d permissions", len(all_perms))

        all_perms = list((await db.execute(select(Permission))).scalars().all())

        # Ensure admin role
        admin_role = (
            await db.execute(
                select(Role).where(Role.name == "admin").options(selectinload(Role.permissions))
            )
        ).scalar_one_or_none()
        if admin_role is None:
            admin_role = Role(name="admin", description="Super admin")
            db.add(admin_role)
            await db.flush()
            logger.info("Created admin role")
        # Use bulk association to avoid lazy-load on newly created roles
        from src.models.user import role_permissions as role_perms_table
        await db.execute(
            role_perms_table.delete().where(role_perms_table.c.role_id == admin_role.id)
        )
        for p in all_perms:
            await db.execute(
                role_perms_table.insert().values(role_id=admin_role.id, permission_id=p.id)
            )

        # Ensure viewer role
        viewer_role = (
            await db.execute(
                select(Role).where(Role.name == "viewer")
            )
        ).scalar_one_or_none()
        if viewer_role is None:
            viewer_role = Role(name="viewer", description="Read-only access")
            db.add(viewer_role)
            await db.flush()
            logger.info("Created viewer role")
        await db.execute(
            role_perms_table.delete().where(role_perms_table.c.role_id == viewer_role.id)
        )
        for p in all_perms:
            if p.action == "view":
                await db.execute(
                    role_perms_table.insert().values(role_id=viewer_role.id, permission_id=p.id)
                )

        await db.flush()

        # Ensure admin user exists with admin role
        admin_user = (
            await db.execute(
                select(User)
                .where(User.username == "admin")
                .options(selectinload(User.roles))
            )
        ).scalar_one_or_none()

        if admin_user is None:
            admin_user = User(
                username="admin",
                email="admin@aiopsos.local",
                hashed_password=hash_password("Golang#3th"),
                is_active=True,
            )
            db.add(admin_user)
            await db.flush()
            logger.info("Created admin user")
            admin_user = (
                await db.execute(
                    select(User)
                    .where(User.id == admin_user.id)
                    .options(selectinload(User.roles))
                )
            ).scalar_one()

        admin_role_ids = {r.id for r in admin_user.roles}
        if admin_role.id not in admin_role_ids:
            admin_user.roles.append(admin_role)
            logger.info("Assigned admin role to admin user")

        await db.commit()
        logger.info(
            "Seed complete: admin user, %d permissions, roles (admin, viewer)",
            len(all_perms),
        )

        # Ensure admin user has a default space
        from src.models.space import Space
        from src.services.space_service import create_default_space_for_user
        result = await db.execute(
            select(Space).where(Space.created_by == admin_user.id)
        )
        if result.first() is None:
            space_id = await create_default_space_for_user(str(admin_user.id))
            if space_id:
                logger.info("Created default space for admin: %s", space_id)


async def seed_all():
    """Run all seed functions."""
    await seed()
    
    # Seed built-in skills from data/skills directory
    from scripts.seed_skills import seed_skills
    await seed_skills()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(seed_all())
