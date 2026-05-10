"""Collaboration Session API endpoints.

This module provides comprehensive API endpoints for managing emergency
collaboration sessions, including:
- Session listing with filtering and pagination
- Session details with messages and recommendations
- Status management and state transitions
- Progress analysis and recommendation generation
- Session report export
- Message search

Requirements covered: 12.1-12.7, 10.5, 11.5, 11.6
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import selectinload

from src.api.deps import (
    DbSession,
    get_current_user,
    get_optional_space_id,
    require_perm,
)
from src.models.collaboration import (
    CollaborationMessage,
    CollaborationRecommendation,
    CollaborationSession,
)
from src.schemas.collaboration import (
    CollaborationMessageListOut,
    CollaborationMessageResponse,
    CollaborationRecommendationListOut,
    CollaborationRecommendationResponse,
    CollaborationRecommendationUpdate,
    CollaborationReportResponse,
    CollaborationSessionListOut,
    CollaborationSessionListResponse,
    CollaborationSessionResponse,
    CollaborationSessionUpdate,
    ProgressAnalysisResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/collaboration-sessions", tags=["collaboration"])


# =============================================================================
# Helper Functions
# =============================================================================


async def _load_session_with_relations(
    db: DbSession,
    session_id: str,
) -> CollaborationSession | None:
    """Load a session with all its relationships.

    Args:
        db: Database session
        session_id: The session ID to load

    Returns:
        The session with loaded relationships, or None if not found
    """
    result = await db.execute(
        select(CollaborationSession)
        .where(CollaborationSession.id == session_id)
        .options(
            selectinload(CollaborationSession.messages),
            selectinload(CollaborationSession.recommendations),
            selectinload(CollaborationSession.scenario),
        )
    )
    return result.scalar_one_or_none()


def _validate_uuid(value: str, field_name: str) -> uuid.UUID:
    """Validate and convert a string to UUID.

    Args:
        value: The string value to validate
        field_name: Name of the field for error messages

    Returns:
        The validated UUID

    Raises:
        HTTPException: If the value is not a valid UUID
    """
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field_name} format: must be a valid UUID",
        )


# =============================================================================
# Session List and Query Endpoints
# =============================================================================


@router.get("", response_model=CollaborationSessionListOut)
async def list_collaboration_sessions(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
    status: str | None = Query(None, description="Filter by status"),
    scenario_id: str | None = Query(None, description="Filter by scenario ID"),
    start_time: datetime | None = Query(None, description="Filter by creation time (start)"),
    end_time: datetime | None = Query(None, description="Filter by creation time (end)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
):
    """List collaboration sessions with filtering and pagination.

    Args:
        db: Database session
        space_id: Optional workspace filter
        status: Optional status filter (created, active, resolved, closed)
        scenario_id: Optional scenario ID filter
        start_time: Optional start time filter
        end_time: Optional end time filter
        page: Page number (1-indexed)
        page_size: Number of items per page

    Returns:
        Paginated list of collaboration sessions

    Requirements: 12.1, 12.2 (pagination and filtering)
    """
    # Build base query
    query = select(CollaborationSession)

    # Apply filters
    filters = []

    if space_id:
        filters.append(
            or_(
                CollaborationSession.space_id == space_id,
                CollaborationSession.space_id.is_(None),
            )
        )

    if status:
        valid_statuses = {"created", "active", "resolved", "closed"}
        if status not in valid_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}",
            )
        filters.append(CollaborationSession.status == status)

    if scenario_id:
        scenario_uuid = _validate_uuid(scenario_id, "scenario_id")
        filters.append(CollaborationSession.scenario_id == scenario_uuid)

    if start_time:
        filters.append(CollaborationSession.created_at >= start_time)

    if end_time:
        filters.append(CollaborationSession.created_at <= end_time)

    if filters:
        query = query.where(and_(*filters))

    # Get total count
    count_query = select(func.count(CollaborationSession.id))
    if filters:
        count_query = count_query.where(and_(*filters))
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Apply pagination and ordering
    skip = (page - 1) * page_size
    query = (
        query.order_by(CollaborationSession.created_at.desc())
        .offset(skip)
        .limit(page_size)
    )

    result = await db.execute(query)
    sessions = result.scalars().all()

    return CollaborationSessionListOut(
        items=sessions,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/count")
async def count_collaboration_sessions(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
    status: str | None = Query(None, description="Filter by status"),
    scenario_id: str | None = Query(None, description="Filter by scenario ID"),
):
    """Get the total count of collaboration sessions matching filters.

    Args:
        db: Database session
        space_id: Optional workspace filter
        status: Optional status filter
        scenario_id: Optional scenario ID filter

    Returns:
        Dictionary with total count and counts by status
    """
    # Build filters
    filters = []

    if space_id:
        filters.append(
            or_(
                CollaborationSession.space_id == space_id,
                CollaborationSession.space_id.is_(None),
            )
        )

    if status:
        valid_statuses = {"created", "active", "resolved", "closed"}
        if status not in valid_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}",
            )
        filters.append(CollaborationSession.status == status)

    if scenario_id:
        scenario_uuid = _validate_uuid(scenario_id, "scenario_id")
        filters.append(CollaborationSession.scenario_id == scenario_uuid)

    # Get total count
    count_query = select(func.count(CollaborationSession.id))
    if filters:
        count_query = count_query.where(and_(*filters))
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Get counts by status
    status_counts = {}
    for s in ["created", "active", "resolved", "closed"]:
        status_query = select(func.count(CollaborationSession.id)).where(
            CollaborationSession.status == s
        )
        if space_id:
            status_query = status_query.where(
                or_(
                    CollaborationSession.space_id == space_id,
                    CollaborationSession.space_id.is_(None),
                )
            )
        if scenario_id:
            status_query = status_query.where(
                CollaborationSession.scenario_id == _validate_uuid(scenario_id, "scenario_id")
            )
        status_result = await db.execute(status_query)
        status_counts[s] = status_result.scalar() or 0

    return {
        "total": total,
        "by_status": status_counts,
    }


# =============================================================================
# Session Detail Endpoints
# =============================================================================


@router.get("/{session_id}", response_model=CollaborationSessionResponse)
async def get_collaboration_session(
    session_id: str,
    db: DbSession,
    _=Depends(get_current_user),
):
    """Get collaboration session details with messages and recommendations.

    Args:
        session_id: The session ID
        db: Database session

    Returns:
        The session with all related data

    Raises:
        HTTPException: 404 if session not found

    Requirements: 12.3 (session details with messages, progress, recommendations)
    """
    session_uuid = _validate_uuid(session_id, "session_id")
    session = await _load_session_with_relations(db, str(session_uuid))

    if session is None:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    return session


@router.get("/{session_id}/messages", response_model=CollaborationMessageListOut)
async def list_session_messages(
    session_id: str,
    db: DbSession,
    _=Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
):
    """List messages for a collaboration session.

    Args:
        session_id: The session ID
        db: Database session
        page: Page number
        page_size: Items per page

    Returns:
        Paginated list of messages

    Requirements: 12.3 (message records)
    """
    session_uuid = _validate_uuid(session_id, "session_id")

    # Verify session exists
    session_result = await db.execute(
        select(CollaborationSession).where(CollaborationSession.id == session_uuid)
    )
    if session_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    # Get total count
    count_query = select(func.count(CollaborationMessage.id)).where(
        CollaborationMessage.session_id == session_uuid
    )
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Get messages with pagination
    skip = (page - 1) * page_size
    query = (
        select(CollaborationMessage)
        .where(CollaborationMessage.session_id == session_uuid)
        .order_by(CollaborationMessage.created_at.asc())
        .offset(skip)
        .limit(page_size)
    )
    result = await db.execute(query)
    messages = result.scalars().all()

    return CollaborationMessageListOut(
        items=messages,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{session_id}/recommendations", response_model=CollaborationRecommendationListOut)
async def list_session_recommendations(
    session_id: str,
    db: DbSession,
    _=Depends(get_current_user),
    status: str | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
):
    """List recommendations for a collaboration session.

    Args:
        session_id: The session ID
        db: Database session
        status: Optional status filter (pending, adopted, ignored, modified)
        page: Page number
        page_size: Items per page

    Returns:
        Paginated list of recommendations

    Requirements: 12.3 (recommendation history)
    """
    session_uuid = _validate_uuid(session_id, "session_id")

    # Verify session exists
    session_result = await db.execute(
        select(CollaborationSession).where(CollaborationSession.id == session_uuid)
    )
    if session_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    # Build query
    query = select(CollaborationRecommendation).where(
        CollaborationRecommendation.session_id == session_uuid
    )

    if status:
        valid_statuses = {"pending", "adopted", "ignored", "modified"}
        if status not in valid_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}",
            )
        query = query.where(CollaborationRecommendation.status == status)

    # Get total count
    count_query = select(func.count(CollaborationRecommendation.id)).where(
        CollaborationRecommendation.session_id == session_uuid
    )
    if status:
        count_query = count_query.where(CollaborationRecommendation.status == status)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Get recommendations with pagination
    skip = (page - 1) * page_size
    query = (
        query.order_by(
            CollaborationRecommendation.priority.desc(),
            CollaborationRecommendation.created_at.desc(),
        )
        .offset(skip)
        .limit(page_size)
    )
    result = await db.execute(query)
    recommendations = result.scalars().all()

    return CollaborationRecommendationListOut(
        items=recommendations,
        total=total,
        page=page,
        page_size=page_size,
    )


# =============================================================================
# Session Management Endpoints
# =============================================================================


@router.put("/{session_id}/status")
async def update_session_status(
    session_id: str,
    body: CollaborationSessionUpdate,
    db: DbSession,
    user=Depends(get_current_user),
):
    """Update collaboration session status.

    Supports status transitions:
    - created -> active
    - active -> resolved
    - resolved -> closed
    - Any status -> closed (force close)

    Args:
        session_id: The session ID
        body: Update data with new status
        db: Database session
        user: Current user

    Returns:
        Updated session

    Requirements: 12.5, 12.6 (manual status update with logging)
    """
    session_uuid = _validate_uuid(session_id, "session_id")

    result = await db.execute(
        select(CollaborationSession).where(CollaborationSession.id == session_uuid)
    )
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    if body.status is None:
        raise HTTPException(status_code=422, detail="status field is required")

    old_status = session.status
    new_status = body.status

    # Validate status transition
    valid_transitions = {
        "created": ["active", "closed"],
        "active": ["resolved", "closed"],
        "resolved": ["closed"],
        "closed": [],  # Cannot transition from closed
    }

    if old_status == "closed":
        raise HTTPException(
            status_code=400,
            detail="Cannot update status of a closed session",
        )

    if new_status not in valid_transitions.get(old_status, []):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status transition from '{old_status}' to '{new_status}'. "
            f"Valid transitions: {', '.join(valid_transitions.get(old_status, []))}",
        )

    # Update status
    session.status = new_status

    # Set timestamps based on new status
    now = datetime.utcnow()
    if new_status == "resolved":
        session.resolved_at = now
    elif new_status == "closed":
        session.closed_at = now

    # Log the status change
    username = getattr(user, "username", None) or getattr(user, "email", None) or str(user.id)
    logger.info(
        f"Collaboration session {session_id} status changed: {old_status} -> {new_status} "
        f"by user {username}"
    )

    await db.commit()
    await db.refresh(session)

    return {
        "id": str(session.id),
        "old_status": old_status,
        "new_status": new_status,
        "updated_by": username,
        "updated_at": now.isoformat(),
    }


# =============================================================================
# Report and Export Endpoints
# =============================================================================


@router.get("/{session_id}/report", response_model=CollaborationReportResponse)
async def get_session_report(
    session_id: str,
    db: DbSession,
    _=Depends(get_current_user),
):
    """Get collaboration session report.

    Generates a summary report including:
    - Session metadata (scenario, status, timestamps)
    - Duration and timing information
    - Message and recommendation statistics
    - Progress summary and key events
    - Participant list

    Args:
        session_id: The session ID
        db: Database session

    Returns:
        Session report data

    Requirements: 12.4 (export collaboration session report)
    """
    session_uuid = _validate_uuid(session_id, "session_id")
    session = await _load_session_with_relations(db, str(session_uuid))

    if session is None:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    # Calculate duration
    duration_minutes = 0
    if session.created_at:
        end_time = session.closed_at or session.resolved_at or datetime.utcnow()
        duration = end_time - session.created_at
        duration_minutes = int(duration.total_seconds() / 60)

    # Count messages and recommendations
    message_count = len(session.messages) if session.messages else 0
    recommendation_count = len(session.recommendations) if session.recommendations else 0
    adopted_recommendations = sum(
        1 for r in (session.recommendations or []) if r.status == "adopted"
    )

    # Get unique participants from messages
    participants = list(
        {m.sender_name or m.sender_id for m in (session.messages or []) if m.sender_name or m.sender_id}
    )

    # Get scenario name
    scenario_name = "Unknown"
    if session.scenario:
        scenario_name = session.scenario.name
    elif session.config_snapshot:
        scenario_name = session.config_snapshot.get("scenario_name", "Unknown")

    # Get progress summary and key events
    progress_summary = session.progress_summary or {}
    key_events = progress_summary.get("key_events", [])

    return CollaborationReportResponse(
        session_id=str(session.id),
        scenario_name=scenario_name,
        status=session.status,
        trigger_reason=session.trigger_reason,
        created_at=session.created_at,
        resolved_at=session.resolved_at,
        closed_at=session.closed_at,
        duration_minutes=duration_minutes,
        message_count=message_count,
        recommendation_count=recommendation_count,
        adopted_recommendations=adopted_recommendations,
        progress_summary=progress_summary,
        key_events=key_events,
        participants=participants,
    )


# =============================================================================
# Search Endpoints
# =============================================================================


@router.get("/search/messages")
async def search_session_messages(
    db: DbSession,
    _=Depends(get_current_user),
    keyword: str = Query(..., min_length=1, description="Search keyword"),
    session_id: str | None = Query(None, description="Limit to specific session"),
    space_id: str | None = Depends(get_optional_space_id),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
):
    """Search collaboration session messages by keyword.

    Args:
        db: Database session
        keyword: Search keyword (searches in message content)
        session_id: Optional session ID to limit search
        space_id: Optional workspace filter
        page: Page number
        page_size: Items per page

    Returns:
        Paginated list of matching messages with session info

    Requirements: 12.7 (keyword search in message content)
    """
    # Build query with join to session for space filtering
    query = (
        select(CollaborationMessage)
        .join(CollaborationSession)
        .where(CollaborationMessage.content.ilike(f"%{keyword}%"))
    )

    filters = []

    if session_id:
        session_uuid = _validate_uuid(session_id, "session_id")
        filters.append(CollaborationMessage.session_id == session_uuid)

    if space_id:
        filters.append(
            or_(
                CollaborationSession.space_id == space_id,
                CollaborationSession.space_id.is_(None),
            )
        )

    if filters:
        query = query.where(and_(*filters))

    # Get total count
    count_query = (
        select(func.count(CollaborationMessage.id))
        .join(CollaborationSession)
        .where(CollaborationMessage.content.ilike(f"%{keyword}%"))
    )
    if filters:
        count_query = count_query.where(and_(*filters))
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # Get messages with pagination
    skip = (page - 1) * page_size
    query = (
        query.order_by(CollaborationMessage.created_at.desc())
        .offset(skip)
        .limit(page_size)
    )
    result = await db.execute(query)
    messages = result.scalars().all()

    # Build response with session info
    items = []
    for msg in messages:
        items.append({
            "id": str(msg.id),
            "session_id": str(msg.session_id),
            "content": msg.content,
            "sender_name": msg.sender_name,
            "source_channel": msg.source_channel,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "highlight": _highlight_keyword(msg.content, keyword),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "keyword": keyword,
    }


def _highlight_keyword(text: str, keyword: str, context_chars: int = 50) -> str:
    """Extract a snippet of text around the keyword match.

    Args:
        text: The full text to search
        keyword: The keyword to highlight
        context_chars: Number of characters to include before/after match

    Returns:
        Snippet with keyword highlighted
    """
    lower_text = text.lower()
    lower_keyword = keyword.lower()
    pos = lower_text.find(lower_keyword)

    if pos == -1:
        return text[:100] + "..." if len(text) > 100 else text

    start = max(0, pos - context_chars)
    end = min(len(text), pos + len(keyword) + context_chars)

    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    return snippet


# =============================================================================
# Progress Analysis and Recommendation Endpoints
# =============================================================================


@router.post("/{session_id}/analyze", response_model=ProgressAnalysisResponse)
async def trigger_progress_analysis(
    session_id: str,
    db: DbSession,
    _=Depends(require_perm("collaboration", "update")),
):
    """Manually trigger progress analysis for a session.

    Analyzes the session's messages and operations to identify:
    - Current phase (investigation, diagnosis, resolution, verification)
    - Completed steps
    - Pending items
    - Key events

    Args:
        session_id: The session ID
        db: Database session

    Returns:
        Progress analysis results

    Requirements: 10.5 (manual trigger of progress analysis)
    """
    session_uuid = _validate_uuid(session_id, "session_id")

    # Verify session exists
    result = await db.execute(
        select(CollaborationSession).where(CollaborationSession.id == session_uuid)
    )
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    # Import and use ProgressAnalyzer
    try:
        from src.services.progress_analyzer import ProgressAnalyzer

        analyzer = ProgressAnalyzer(db)
        analysis_result = await analyzer.analyze_session(session_uuid)

        # Update session with analysis results
        session.progress_summary = analysis_result.to_dict()
        await db.commit()

        return ProgressAnalysisResponse(
            session_id=str(session_uuid),
            current_phase=analysis_result.current_phase,
            completed_steps=analysis_result.completed_steps,
            pending_items=analysis_result.pending_items,
            duration_minutes=analysis_result.duration_minutes,
            key_events=[e.to_dict() for e in analysis_result.key_events],
            last_analysis_at=datetime.utcnow(),
        )

    except ImportError:
        logger.warning("ProgressAnalyzer not available, returning current progress_summary")
        progress = session.progress_summary or {}
        return ProgressAnalysisResponse(
            session_id=str(session_uuid),
            current_phase=progress.get("current_phase"),
            completed_steps=progress.get("completed_steps", []),
            pending_items=progress.get("pending_items", []),
            duration_minutes=progress.get("duration_minutes", 0),
            key_events=progress.get("key_events", []),
            last_analysis_at=None,
        )
    except Exception as exc:
        logger.exception(f"Progress analysis failed for session {session_id}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Progress analysis failed: {exc}",
        )


@router.post("/{session_id}/generate-recommendations")
async def generate_recommendations(
    session_id: str,
    db: DbSession,
    _=Depends(require_perm("collaboration", "update")),
):
    """Generate recommendations for a collaboration session.

    Uses the RecommendationEngine to generate next-step recommendations
    based on the current progress analysis.

    Args:
        session_id: The session ID
        db: Database session

    Returns:
        Generated recommendations

    Requirements: 11.5 (get recommendations)
    """
    session_uuid = _validate_uuid(session_id, "session_id")

    # Verify session exists
    result = await db.execute(
        select(CollaborationSession).where(CollaborationSession.id == session_uuid)
    )
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(status_code=404, detail="Collaboration session not found")

    # Import and use RecommendationEngine
    try:
        from src.services.recommendation_engine import RecommendationEngine

        engine = RecommendationEngine(db)
        rec_result = await engine.generate_recommendations(session_uuid)

        return {
            "session_id": str(session_uuid),
            "recommendations": [r.to_dict() for r in rec_result.recommendations],
            "generated_at": rec_result.generation_timestamp,
            "error": rec_result.error,
        }

    except ImportError:
        logger.warning("RecommendationEngine not available")
        raise HTTPException(
            status_code=503,
            detail="Recommendation engine not available",
        )
    except Exception as exc:
        logger.exception(f"Recommendation generation failed for session {session_id}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Recommendation generation failed: {exc}",
        )


@router.put("/recommendations/{recommendation_id}/feedback")
async def update_recommendation_feedback(
    recommendation_id: str,
    body: CollaborationRecommendationUpdate,
    db: DbSession,
    user=Depends(get_current_user),
):
    """Update feedback for a recommendation.

    Allows users to provide feedback on recommendations:
    - adopted: User adopted the recommendation
    - ignored: User chose to ignore the recommendation
    - modified: User modified and then adopted the recommendation

    Args:
        recommendation_id: The recommendation ID
        body: Feedback data
        db: Database session
        user: Current user

    Returns:
        Updated recommendation

    Requirements: 11.5, 11.6 (recommendation feedback)
    """
    rec_uuid = _validate_uuid(recommendation_id, "recommendation_id")

    result = await db.execute(
        select(CollaborationRecommendation).where(CollaborationRecommendation.id == rec_uuid)
    )
    recommendation = result.scalar_one_or_none()

    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    # Update fields
    if body.status:
        recommendation.status = body.status
        if body.status == "adopted":
            recommendation.adopted_at = datetime.utcnow()

    if body.feedback:
        recommendation.feedback = body.feedback

    await db.commit()
    await db.refresh(recommendation)

    username = getattr(user, "username", None) or getattr(user, "email", None) or str(user.id)
    logger.info(
        f"Recommendation {recommendation_id} feedback updated: status={body.status}, "
        f"by user {username}"
    )

    return recommendation
