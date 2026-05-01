"""Space service — auto-create default space on register, clone templates,
and send space-related notifications."""

import logging

from sqlalchemy import select

from src.models.base import async_session_factory
from src.models.notification import Notification
from src.models.space import Space, SpaceInvitation, SpaceJoinRequest, SpaceMember
from src.models.user import User

logger = logging.getLogger(__name__)

DEFAULT_SPACE_NAME = "我的空间"


async def create_default_space_for_user(user_id: str) -> str | None:
    """Create a default private space for a new user. Returns space_id or None."""
    try:
        async with async_session_factory() as db:
            space = Space(
                name=DEFAULT_SPACE_NAME,
                description="个人默认空间",
                visibility="private",
                created_by=user_id,
            )
            db.add(space)
            await db.flush()

            member = SpaceMember(space_id=space.id, user_id=user_id, role="admin")
            db.add(member)

            user = await db.scalar(select(User).where(User.id == user_id))
            if user:
                user.default_space_id = space.id

            await db.commit()
            logger.info("Default space created for user %s: %s", user_id, space.id)
            return str(space.id)
    except Exception:
        logger.exception("Failed to create default space for user %s", user_id)
        return None


async def clone_templates_to_space(db, space_id: str) -> None:
    """Clone system template agents (space_id=null) into the new space."""
    try:
        from src.models.agent import Agent, agent_sub_agents, agent_tools

        result = await db.execute(
            select(Agent).where(Agent.space_id == None, Agent.type == "main")
        )
        template = result.scalar_one_or_none()
        if template is None:
            return

        main_agent = Agent(
            name=template.name,
            type=template.type,
            system_prompt=template.system_prompt,
            user_prompt=template.user_prompt,
            model_name=template.model_name,
            agent_type=template.agent_type,
            config=dict(template.config),
            is_active=template.is_active,
            model_provider_id=template.model_provider_id,
            space_id=space_id,
        )
        db.add(main_agent)
        await db.flush()

        # Clone main agent's tool associations
        main_tools = await db.execute(
            select(agent_tools.c.tool_id).where(agent_tools.c.agent_id == template.id)
        )
        for (tool_id,) in main_tools:
            await db.execute(
                agent_tools.insert().values(agent_id=main_agent.id, tool_id=tool_id)
            )

        sub_result = await db.execute(
            select(Agent).where(Agent.space_id == None, Agent.type == "sub")
        )
        for sub_template in sub_result.scalars().all():
            sub = Agent(
                name=sub_template.name,
                type=sub_template.type,
                system_prompt=sub_template.system_prompt,
                user_prompt=sub_template.user_prompt,
                model_name=sub_template.model_name,
                agent_type=sub_template.agent_type,
                config=dict(sub_template.config),
                is_active=sub_template.is_active,
                model_provider_id=sub_template.model_provider_id,
                space_id=space_id,
            )
            db.add(sub)
            await db.flush()

            # Clone sub agent's tool associations
            sub_tools = await db.execute(
                select(agent_tools.c.tool_id).where(agent_tools.c.agent_id == sub_template.id)
            )
            for (tool_id,) in sub_tools:
                await db.execute(
                    agent_tools.insert().values(agent_id=sub.id, tool_id=tool_id)
                )

            await db.execute(
                agent_sub_agents.insert().values(
                    main_agent_id=main_agent.id, sub_agent_id=sub.id
                )
            )

        await db.commit()
        logger.info("Templates cloned to space %s", space_id)
    except Exception:
        logger.exception("Failed to clone templates to space %s", space_id)


async def send_invitation_notification(db, invitation: SpaceInvitation) -> None:
    """Create in-app notification and send email for space invitation."""
    try:
        space = await db.scalar(select(Space.name).where(Space.id == invitation.space_id))
        inviter = await db.scalar(select(User.username).where(User.id == invitation.inviter_id))
        space_name = space or "未知空间"

        notif = Notification(
            user_id=invitation.invitee_id,
            title="邀请加入空间",
            message=f"{inviter or '管理员'} 邀请你加入空间「{space_name}」",
            severity="info",
            category="space_invite",
        )
        db.add(notif)
        await db.commit()

        try:
            invitee = await db.scalar(
                select(User.email).where(User.id == invitation.invitee_id)
            )
            if invitee:
                from src.services.channel_manager import channel_manager
                await channel_manager.send(
                    channel_type="email",
                    config={},
                    title=f"邀请加入空间「{space_name}」",
                    message=f"{inviter or '管理员'} 邀请你加入空间「{space_name}」，请登录 AIOpsOS 查看。",
                    severity="info",
                    recipients=[invitee],
                )
        except Exception:
            logger.exception("Failed to send invitation email")
    except Exception:
        logger.exception("Failed to send invitation notification")


async def notify_admins_join_request(db, space_id, req: SpaceJoinRequest) -> None:
    """Notify all space admins about a new join request."""
    try:
        space = await db.scalar(select(Space.name).where(Space.id == space_id))
        applicant = await db.scalar(select(User.username).where(User.id == req.user_id))
        space_name = space or "未知空间"

        admin_result = await db.execute(
            select(SpaceMember.user_id).where(
                SpaceMember.space_id == space_id, SpaceMember.role == "admin"
            )
        )
        admin_ids = [row[0] for row in admin_result.fetchall()]

        for admin_id in admin_ids:
            notif = Notification(
                user_id=admin_id,
                title="新的加入申请",
                message=f"{applicant or '用户'} 申请加入空间「{space_name}」",
                severity="info",
                category="space_request",
            )
            db.add(notif)
        await db.commit()
    except Exception:
        logger.exception("Failed to notify admins of join request")


async def notify_join_request_result(db, req: SpaceJoinRequest) -> None:
    """Notify applicant about approval/rejection."""
    try:
        space = await db.scalar(select(Space.name).where(Space.id == req.space_id))
        space_name = space or "未知空间"
        status_text = "已通过" if req.status == "approved" else "已拒绝"

        notif = Notification(
            user_id=req.user_id,
            title=f"加入申请{status_text}",
            message=f"你加入空间「{space_name}」的申请{status_text}",
            severity="info",
            category="space_request",
        )
        db.add(notif)
        await db.commit()
    except Exception:
        logger.exception("Failed to notify join request result")
