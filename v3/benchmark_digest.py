from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence


def install(scanner_namespace: Dict[str, Any]) -> None:
    """Install benchmark-aware digest selection without changing signal scores.

    Configured benchmark symbols are guaranteed a visible digest slot whenever
    they have a non-terminal reportable signal, including an ``extended`` setup
    that would otherwise appear only in the CSV/dashboard.
    """

    config = scanner_namespace["config"]
    original_compact_line = scanner_namespace["compact_candidate_line"]

    def priority_symbols() -> List[str]:
        return [str(symbol).upper() for symbol in getattr(config, "PRIORITY_DIGEST_SYMBOLS", ())]

    def is_priority(candidate: Any) -> bool:
        return str(candidate.symbol).upper() in set(priority_symbols())

    def build_delivery_plan(candidates: Sequence[Any]) -> Dict[str, Any]:
        terminal = {"invalidated", "completed"}
        eligible = [candidate for candidate in candidates if candidate.lifecycle_status not in terminal]
        ranked = sorted(eligible, key=lambda item: (-item.score, item.grade, item.symbol))

        report_cap = max(1, int(config.MAX_REPORT_SIGNALS))
        full_report = list(ranked[:report_cap])
        full_ids = {candidate.id for candidate in full_report}

        ordered_priority = []
        by_symbol = {str(candidate.symbol).upper(): candidate for candidate in ranked}
        for symbol in priority_symbols():
            candidate = by_symbol.get(symbol)
            if candidate is None:
                continue
            ordered_priority.append(candidate)
            if candidate.id in full_ids:
                continue
            replace_index = next(
                (index for index in range(len(full_report) - 1, -1, -1) if not is_priority(full_report[index])),
                None,
            )
            if replace_index is not None:
                full_ids.discard(full_report[replace_index].id)
                full_report[replace_index] = candidate
                full_ids.add(candidate.id)

        digest_statuses = {"confirmed", "active", "entry_zone", "unknown"}
        normal_digest = [candidate for candidate in full_report if candidate.lifecycle_status in digest_statuses]
        digest_cap = max(1, int(config.MAX_DIGEST_SIGNALS))
        priority_in_report = [candidate for candidate in ordered_priority if candidate.id in full_ids]
        priority_ids = {candidate.id for candidate in priority_in_report}
        digest = priority_in_report + [candidate for candidate in normal_digest if candidate.id not in priority_ids]
        digest = digest[:digest_cap]

        details = [
            candidate for candidate in digest
            if candidate.score >= int(config.MIN_SCORE_FOR_DETAIL)
        ][: int(config.TOP_DETAILED_ALERTS)]
        digest_messages = math.ceil(len(digest) / max(1, int(config.DIGEST_SIGNALS_PER_MESSAGE))) if digest else 0
        expected_messages = digest_messages + len(details) + 1
        if full_report:
            expected_messages += 1

        return {
            "full_report": full_report,
            "digest": digest,
            "details": details,
            "expected_messages": expected_messages,
            "digest_messages": digest_messages,
            "priority_digest_count": sum(candidate.id in priority_ids for candidate in digest),
        }

    def compact_candidate_line(candidate: Any) -> str:
        line = original_compact_line(candidate)
        return f"📌 {line}" if is_priority(candidate) else line

    scanner_namespace["build_delivery_plan"] = build_delivery_plan
    scanner_namespace["compact_candidate_line"] = compact_candidate_line
