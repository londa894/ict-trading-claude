from __future__ import annotations

from datetime import datetime

from app.domain.base import ApiModel
from app.domain.enums import IssueSeverity, ValidationIssueCode


class ValidationIssue(ApiModel):
    code: ValidationIssueCode
    severity: IssueSeverity
    message: str
    at: datetime | None = None
    count: int = 1


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.severity is IssueSeverity.ERROR for i in issues)
