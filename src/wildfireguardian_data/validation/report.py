"""Validation findings and reports.

Severity is a scientific judgement, not a formatting choice
(``AGENTS.md`` §2):

* **ERROR** -- the data is wrong, or its meaning is unknowable. A bundle with
  an ERROR must not be used.
* **WARNING** -- the data is usable but incomplete or surprising. An `UNKNOWN`
  provenance field is a WARNING: an honest gap is a finding, whereas a
  fabricated value would be a defect (``docs/DECISIONS.md`` D-0009).
* **INFO** -- context a reader needs in order to interpret the data correctly.

Promoting an ERROR to a WARNING, or deleting a check, changes what this
repository claims. It requires a ``docs/DECISIONS.md`` entry.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = ["Severity", "Finding", "ValidationReport", "REPORT_SCHEMA_VERSION"]

REPORT_SCHEMA_VERSION = "1.0.0"


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"

    @property
    def rank(self) -> int:
        return {"ERROR": 3, "WARNING": 2, "INFO": 1}[self.value]


@dataclass(frozen=True)
class Finding:
    """One validation finding.

    ``code`` is stable and greppable (``"CRS-002"``), so a downstream consumer
    or a CI job can act on a specific finding without matching prose.
    """

    code: str
    severity: Severity
    message: str
    layer: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "severity", Severity(self.severity))
        object.__setattr__(self, "detail", dict(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "layer": self.layer,
            "detail": self.detail,
        }

    def __str__(self) -> str:
        where = f" [{self.layer}]" if self.layer else ""
        return f"{self.severity.value} {self.code}{where}: {self.message}"


@dataclass
class ValidationReport:
    """A collection of findings, plus the target they concern."""

    target: str
    findings: list[Finding] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def add(
        self,
        code: str,
        severity: Severity,
        message: str,
        *,
        layer: str | None = None,
        **detail: Any,
    ) -> Finding:
        finding = Finding(code=code, severity=severity, message=message, layer=layer, detail=detail)
        self.findings.append(finding)
        return finding

    def extend(self, findings: Iterable[Finding]) -> None:
        self.findings.extend(findings)

    # -- queries ------------------------------------------------------------ #
    def of_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity is severity]

    @property
    def errors(self) -> list[Finding]:
        return self.of_severity(Severity.ERROR)

    @property
    def warnings(self) -> list[Finding]:
        return self.of_severity(Severity.WARNING)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def counts(self) -> dict[str, int]:
        return {
            severity.value: len(self.of_severity(severity)) for severity in Severity
        }

    def status(self, *, strict: bool = False) -> str:
        """``"fail"`` / ``"pass_with_warnings"`` / ``"pass"``.

        Under ``strict``, warnings fail too -- the setting a CI job for a
        published bundle should use.
        """
        if self.has_errors:
            return "fail"
        if self.warnings:
            return "fail" if strict else "pass_with_warnings"
        return "pass"

    def to_dict(self, *, strict: bool = False) -> dict[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "target": self.target,
            "status": self.status(strict=strict),
            "strict": strict,
            "counts": self.counts,
            "context": dict(self.context),
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_text(self) -> str:
        if not self.findings:
            return f"{self.target}: no findings"
        ordered = sorted(
            self.findings, key=lambda f: (-f.severity.rank, f.code, f.layer or "")
        )
        lines = [f"{self.target}: {self.counts}"]
        lines.extend(f"  {finding}" for finding in ordered)
        return "\n".join(lines)
