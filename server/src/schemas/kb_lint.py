"""Lint result schemas for knowledge base quality checks."""

from pydantic import BaseModel


class LintIssue(BaseModel):
    check_id: str
    severity: str  # "error" | "warning" | "info"
    page: str = ""
    message: str
    fix_action: str = ""
    fix_description: str = ""


class LintReport(BaseModel):
    health_score: int  # 0-100
    total_issues: int
    errors: int
    warnings: int
    info: int
    issues: list[LintIssue]
    checked_at: str = ""


class LintFixRequest(BaseModel):
    issue_id: str


class LintFixResponse(BaseModel):
    ok: bool
    issue_id: str
    message: str = ""
