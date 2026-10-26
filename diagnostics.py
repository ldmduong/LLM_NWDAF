"""Evidence collection for UE registration failures (rule based, read only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from prom_query import get_scrape_health, query_prometheus


BASE_DIR = Path(__file__).resolve().parent
ERROR_PATTERN = re.compile(
    r"\b(error|failed|failure|timeout|timed out|reject|rejected|http(?:/\d(?:\.\d)?)?\s+5\d\d)\b",
    re.IGNORECASE,
)
SUPI_PATTERN = re.compile(r"(?i)(?:imsi-)?\d{14,16}")
TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)


def collect_registration_failure_evidence(window_minutes: int = 15) -> dict[str, Any]:
    """Collect recent registration-related log evidence and Prometheus target status."""
    if window_minutes < 1:
        raise ValueError("window_minutes must be positive")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=window_minutes)
    sources = {
        "UERANSIM": [BASE_DIR / "UERANSIM" / "log"],
        "Free5GC": [BASE_DIR / "free5gc" / "log", BASE_DIR / "free5gc" / "NFs"],
        "NWDAF": [BASE_DIR / "mnc_NWDAF-main" / "log"],
    }
    evidence: list[dict[str, Any]] = []
    scanned_files: list[str] = []
    for component, roots in sources.items():
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.log"):
                try:
                    # Do not scan old logs outside the requested window.
                    if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                        continue
                    scanned_files.append(str(path.relative_to(BASE_DIR)))
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
                except OSError:
                    continue
                for line in lines:
                    if not ERROR_PATTERN.search(line) or not re.search(r"registration|register|auth|identity|ue", line, re.I):
                        continue
                    stamp_match = TIMESTAMP_PATTERN.search(line)
                    timestamp = stamp_match.group(0) if stamp_match else None
                    evidence.append({
                        "component": component,
                        "file": str(path.relative_to(BASE_DIR)),
                        "timestamp": timestamp,
                        "time_correlated": timestamp is not None,
                        "evidence": SUPI_PATTERN.sub("[UE_ID_REDACTED]", line)[:500],
                    })

    scrape = get_scrape_health("amf_ue_registration_state")
    try:
        registration_metric = query_prometheus(
            "amf_ue_registration_state", window_minutes=window_minutes
        )
    except Exception as exc:
        registration_metric = {"status": "error", "error": str(exc)}
    correlated_count = sum(item["time_correlated"] for item in evidence)
    return {
        "incident": "UE registration failure",
        "status": "evidence_found" if evidence else "insufficient_evidence",
        "window_minutes": window_minutes,
        "collected_at": now.isoformat(),
        "components": list(sources),
        "scanned_files": sorted(set(scanned_files)),
        "scrape_health": scrape,
        "registration_metric": registration_metric,
        "evidence": evidence[-100:],
        "confidence": "low" if evidence and correlated_count else "insufficient",
        "next_checks": [
            "Compare timestamps across UE, AMF, NWDAF, and Prometheus targets.",
            "Check the AMF and NWDAF scrape target health before treating metrics as evidence.",
            "If timestamps, logs, or metrics are missing or conflict, collect a matching time window before concluding.",
        ],
        "conclusion": "No root cause is asserted by this rule-based collector.",
    }
