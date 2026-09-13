"""
Orchestration — Verification Feedback Formatter.

Converts a VerificationReport into structured, length-bounded diagnostic text
for repair prompts.
"""
from __future__ import annotations

from app.verification.models import CheckStatus, VerificationReport


def format_verification_feedback(
    report: VerificationReport | None,
    max_length: int = 4096,
) -> str:
    """
    Produce structured, bounded diagnostic feedback from a VerificationReport.

    SECURITY INVARIANTS:
    - Hard length limit (max_length, default 4096 characters).
    - Contains only check names, categories, statuses, failure reasons, and bounded output.
    - Exposes no secrets or untrusted raw system paths outside the workspace.
    - Treated as untrusted diagnostic data by downstream prompt formatters.
    """
    if report is None:
        return ""

    lines: list[str] = [
        "### PREVIOUS VERIFICATION FAILURE DIAGNOSTICS",
        f"Gate Verdict: {report.gate_decision.value}",
    ]

    failed_checks = [
        r
        for r in report.results
        if r.status in (CheckStatus.FAIL, CheckStatus.ERROR, CheckStatus.SKIPPED)
    ]

    if not failed_checks:
        lines.append("All checks passed or no failure details reported.")
    else:
        for check in failed_checks:
            lines.append(f"- Check: '{check.check_name}' (ID: {check.check_id})")
            lines.append(f"  Status: {check.status.value}")
            reason = getattr(check, "failure_reason", None) or check.error_message
            if reason:
                lines.append(f"  Reason: {reason}")
            if check.stdout and check.stdout.strip():
                trunc_out = check.stdout.strip()[:512]
                lines.append(f"  Output: {trunc_out}")

    result_str = "\n".join(lines)
    if len(result_str) > max_length:
        result_str = result_str[: max_length - 30] + "\n...[diagnostics truncated]"

    return result_str
