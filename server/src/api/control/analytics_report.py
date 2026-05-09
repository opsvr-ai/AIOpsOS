"""Multi-step analytics report generation with LLM analysis, preview, and PDF export."""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text

from src.agent.sub_agents.analytics_report_agent import AnalyticsReportAgent
from src.api.deps import get_current_user
from src.models.base import async_session_factory
from src.models.report import Report
from src.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

_TEMPLATE_PATH = "src/templates/report_base.html"
_REPORT_TITLE = "AIOpsOS 平台运营分析报告"


def _patch_os2_unicode_range():
    """Work around fontTools OS/2 UnicodeRange bit 123 rejection."""
    import fontTools.ttLib.tables.O_S_2f_2 as os2_module

    original = os2_module.table_O_S_2f_2.setUnicodeRanges

    def _patched(self, bits):
        bits = {b for b in bits if 0 <= b <= 122}
        if not bits:
            bits = {0}
        original(self, bits)

    os2_module.table_O_S_2f_2.setUnicodeRanges = _patched


def _render_html(body_content: str, title: str, theme: str = "indigo") -> str:
    with open(_TEMPLATE_PATH) as f:
        template = f.read()
    return (
        template.replace("{{TITLE}}", title)
        .replace("{{THEME}}", theme)
        .replace("{{GENERATED_AT}}", datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"))
        .replace("{{BODY_CONTENT}}", body_content)
    )


def _fmt(n) -> str:
    if n is None:
        return "0"
    return f"{n:,}"


def _pct(part, total) -> str:
    if not total:
        return "0%"
    return f"{part / total * 100:.0f}%"


def _stat(label: str, value: str, cls: str = "") -> str:
    c = f" {cls}" if cls else ""
    return (
        f'<div class="stat-card{c}">'
        f'<div class="n">{value}</div><div class="l">{label}</div></div>'
    )


def _build_body(analysis_text: str, overview: dict, trends: list,
                top_users: list, top_spaces: list, spaces: list) -> str:
    users = overview["users"]
    sessions = overview["sessions"]
    fb = overview["feedback"]

    stat_cards = (
        '<div class="stat-row">'
        + _stat(f"总用户 · 活跃 {_fmt(users['active'])} · 待激活 {_fmt(users['pending'])}",
                _fmt(users['total']))
        + _stat(f"总会话 · 活跃 {_fmt(sessions['active'])} · 今日 {_fmt(sessions['today'])}",
                _fmt(sessions['total']), "info")
        + _stat("总消息数", _fmt(overview['messages']['total']))
        + _stat("空间总数", _fmt(overview['spaces']['total']))
        + "</div>"
        '<div class="stat-row">'
        + _stat(f"Bug 反馈 · 未关闭 {_fmt(fb['open_bugs'])}",
                _fmt(fb['bugs']), "critical")
        + _stat("Feature 请求", _fmt(fb['features']), "warning")
        + _stat("邀请注册用户", _fmt(users['invited']), "success")
        + _stat("用户活跃率", _pct(users['active'], users['total']), "info")
        + "</div>"
    )

    user_rows = ""
    top_users_adapted = []
    for u in top_users[:10]:
        name = u.get('display_name') or u.get('name') or u.get('username', '')
        turns = u.get('total_turns') or u.get('tt', 0)
        sc = u.get('session_count') or u.get('sc', 0)
        top_users_adapted.append({"name": name, "total_turns": turns, "session_count": sc})
    for i, u in enumerate(top_users_adapted, 1):
        user_rows += (
            f"<tr><td>{i}</td><td>{u['name']}</td>"
            f"<td>{_fmt(u['total_turns'])}</td><td>{_fmt(u['session_count'])}</td></tr>"
        )

    space_rows = ""
    for i, s in enumerate(top_spaces[:10], 1):
        la = s.get('last_active', '')[:10] if s.get('last_active') else '-'
        space_rows += (
            f"<tr><td>{i}</td><td>{s['name']}</td>"
            f"<td>{_fmt(s['session_count'])}</td><td>{la}</td></tr>"
        )

    total_reg = sum(t["registrations"] for t in trends)
    total_sess_t = sum(t["sessions"] for t in trends)
    total_msg = sum(t["messages"] for t in trends)
    peak_msg = max(trends, key=lambda t: t["messages"]) if trends else {"day": "-", "messages": 0}
    peak_sess = max(trends, key=lambda t: t["sessions"]) if trends else {"day": "-", "sessions": 0}
    trend_days = len(trends)

    trend_html = (
        '<div class="kpi-4">'
        + _stat(f"{trend_days}天新增注册", _fmt(total_reg), "info")
        + _stat(f"{trend_days}天新会话", _fmt(total_sess_t), "success")
        + _stat(f"{trend_days}天消息量", _fmt(total_msg))
        + _stat("日均消息", _fmt(int(total_msg / max(trend_days, 1))), "warning")
        + "</div>"
        '<div class="callout info">'
        "<strong>峰值洞察</strong>"
        f"消息峰值日: {peak_msg['day']}（{_fmt(peak_msg['messages'])} 条）&nbsp;·&nbsp;"
        f"会话峰值日: {peak_sess['day']}（{_fmt(peak_sess['sessions'])} 个）"
        "</div>"
    )

    space_detail_rows = ""
    for s in spaces[:15]:
        la = s.get('last_active', '')[:10] if s.get('last_active') else '-'
        ca = s.get('created_at', '')[:10] if s.get('created_at') else '-'
        space_detail_rows += (
            f"<tr><td>{s['name']}</td><td>{ca}</td>"
            f"<td>{_fmt(s['member_count'])}</td><td>{_fmt(s['admin_count'])}</td>"
            f"<td>{_fmt(s['session_count'])}</td><td>{la}</td></tr>"
        )

    sections = _parse_analysis(analysis_text)

    return f"""
    <div class="section-title">核心指标总览</div>
    {stat_cards}

    <div class="section-title">执行摘要</div>
    {sections.get('summary', '<p>暂无分析摘要。</p>')}

    <div class="section-title">趋势洞察</div>
    {trend_html}
    {sections.get('trends', '')}

    <div class="section-title">用户 & 会话分析</div>
    {sections.get('user_analysis', '')}
    <h3>最活跃用户 Top 10</h3>
    <div class="table-wrapper">
      <table><thead><tr><th>#</th><th>用户</th><th>总轮次</th><th>会话数</th></tr></thead>
      <tbody>{user_rows}</tbody></table>
    </div>

    <div class="section-title">空间分析</div>
    {sections.get('space_analysis', '')}
    <h3>最活跃空间 Top 10</h3>
    <div class="table-wrapper">
      <table><thead><tr><th>#</th><th>空间</th><th>会话数</th><th>最近活跃</th></tr></thead>
      <tbody>{space_rows}</tbody></table>
    </div>

    <h3>全部空间概览</h3>
    <div class="table-wrapper">
      <table><thead><tr><th>名称</th><th>创建时间</th><th>成员</th><th>管理员</th><th>会话</th><th>最近活跃</th></tr></thead>
      <tbody>{space_detail_rows}</tbody></table>
    </div>
    <p class="text-muted">共 {len(spaces)} 个空间（仅显示前 15 个）</p>

    <div class="section-title">反馈分析</div>
    <div class="kpi-3">
      {_stat("Bug 总数", _fmt(fb['bugs']), "critical")}
      {_stat("未关闭 Bug", _fmt(fb['open_bugs']), "critical")}
      {_stat("Feature 请求", _fmt(fb['features']), "warning")}
    </div>
    {sections.get('feedback', '')}

    <div class="section-title">运营建议</div>
    {sections.get('recommendations', '<p>暂无建议。</p>')}
    """


def _parse_analysis(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    markers = [
        ("执行摘要", "summary"), ("核心指标", "metrics"),
        ("用户分析", "user_analysis"), ("空间分析", "space_analysis"),
        ("趋势", "trends"), ("反馈", "feedback"), ("运营建议", "recommendations"),
    ]
    lines = text.split("\n")
    current_key = "summary"
    current: list[str] = []

    for line in lines:
        s = line.strip()
        matched = None
        for kw, key in markers:
            if s.startswith("##") and kw in s:
                matched = key
                break
        if matched and current:
            sections[current_key] = _md_to_html(current)
            current_key = matched
            current = []
        else:
            current.append(line)

    if current:
        sections[current_key] = _md_to_html(current)

    return sections


def _md_to_html(lines: list[str]) -> str:
    parts: list[str] = []
    in_list = False

    def _close_list():
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for line in lines:
        s = line.strip()
        if not s:
            _close_list()
            continue

        if s.startswith("### "):
            _close_list()
            parts.append(f"<h3>{s[4:]}</h3>")

        elif s.startswith("- ") or s.startswith("* "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{s[2:]}</li>")

        elif s[0].isdigit() and ". " in s[:4]:
            _close_list()
            num, rest = s.split(". ", 1)
            parts.append(
                f'<div class="callout"><strong>{num}.</strong> {rest}</div>'
            )

        else:
            _close_list()
            parts.append(f"<p>{s}</p>")

    _close_list()
    return "\n".join(parts)


# ── Request/Response schemas ──────────────────────────


class GenerateRequest(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD


class RefineRequest(BaseModel):
    feedback: str


class GenerateResponse(BaseModel):
    report_id: str
    html_content: str
    title: str


class HistoryItem(BaseModel):
    id: str
    title: str
    date_range_start: str | None
    date_range_end: str | None
    created_at: str


# ── Endpoints ─────────────────────────────────────────


@router.post("/admin/analytics/report/generate", response_model=GenerateResponse)
async def generate_report(body: GenerateRequest, user: User = Depends(get_current_user)):
    """Generate an analytics report for the given date range. Returns HTML for preview."""
    agent = AnalyticsReportAgent()
    analysis_text, data = await agent.generate(body.start_date, body.end_date)

    title = f"AIOpsOS 运营分析报告 ({body.start_date} ~ {body.end_date})"
    body_html = _build_body(
        analysis_text, data, data.get("trends", []),
        data.get("top_users", []), data.get("top_spaces", []),
        data.get("space_details", []),
    )
    html = _render_html(body_html, _REPORT_TITLE, "indigo")

    async with async_session_factory() as db:
        report = Report(
            id=uuid.uuid4(),
            user_id=user.id,
            title=title,
            description=f"运营分析报告 {body.start_date} ~ {body.end_date}",
            html_content=html,
            theme="indigo",
            status="published",
            report_type="analytics",
            date_range_start=body.start_date,
            date_range_end=body.end_date,
        )
        db.add(report)
        await db.commit()
        report_id = str(report.id)

    return GenerateResponse(report_id=report_id, html_content=html, title=title)


@router.post("/admin/analytics/report/{report_id}/refine", response_model=GenerateResponse)
async def refine_report(
    report_id: str, body: RefineRequest, user: User = Depends(get_current_user)
):
    """Refine an existing report based on user feedback."""
    async with async_session_factory() as db:
        report = await db.get(Report, uuid.UUID(report_id))
        if not report or str(report.user_id) != str(user.id):
            raise HTTPException(404, "Report not found")

    start_date = report.date_range_start or ""
    end_date = report.date_range_end or ""
    if not start_date or not end_date:
        raise HTTPException(400, "Report missing date range — cannot refine")

    agent = AnalyticsReportAgent()
    analysis_text, data = await agent.generate(start_date, end_date, feedback=body.feedback)

    title = f"{report.title} (已调整)"
    body_html = _build_body(
        analysis_text, data, data.get("trends", []),
        data.get("top_users", []), data.get("top_spaces", []),
        data.get("space_details", []),
    )
    html = _render_html(body_html, _REPORT_TITLE, "indigo")

    async with async_session_factory() as db:
        refined = Report(
            id=uuid.uuid4(),
            user_id=user.id,
            title=title,
            description=body.feedback[:200],
            html_content=html,
            theme="indigo",
            status="published",
            report_type="analytics",
            date_range_start=start_date,
            date_range_end=end_date,
        )
        db.add(refined)
        await db.commit()
        refined_id = str(refined.id)

    return GenerateResponse(report_id=refined_id, html_content=html, title=title)


@router.post("/admin/analytics/report/{report_id}/pdf")
async def download_report_pdf(
    report_id: str, user: User = Depends(get_current_user)
):
    """Convert a report's HTML to PDF and return the file."""
    async with async_session_factory() as db:
        report = await db.get(Report, uuid.UUID(report_id))
        if not report or str(report.user_id) != str(user.id):
            raise HTTPException(404, "Report not found")

    try:
        import weasyprint
        pdf_bytes = weasyprint.HTML(string=report.html_content).write_pdf()
    except ValueError:
        _patch_os2_unicode_range()
        import weasyprint
        pdf_bytes = weasyprint.HTML(string=report.html_content).write_pdf()
    except Exception as e:
        logger.error("PDF generation failed: %s", e)
        raise HTTPException(500, "PDF generation failed") from e

    today = datetime.now(UTC).date()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=AIOpsOS-analytics-report-{today}.pdf",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/admin/analytics/report/history", response_model=list[HistoryItem])
async def list_report_history(user: User = Depends(get_current_user)):
    """List all analytics reports for the current user, newest first."""
    async with async_session_factory() as db:
        rows = await db.execute(
            text(
                "SELECT id, title, date_range_start, date_range_end, created_at "
                "FROM reports "
                "WHERE user_id = CAST(:uid AS uuid) AND report_type = 'analytics' "
                "ORDER BY created_at DESC LIMIT 50"
            ),
            {"uid": str(user.id)},
        )
        reports = rows.fetchall()

    return [
        HistoryItem(
            id=str(r.id),
            title=r.title,
            date_range_start=r.date_range_start,
            date_range_end=r.date_range_end,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in reports
    ]
