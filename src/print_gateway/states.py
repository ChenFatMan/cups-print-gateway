from __future__ import annotations

from typing import Final

UPLOADED: Final = "uploaded"
QUEUED_FOR_CONVERSION: Final = "queued_for_conversion"
CONVERTING: Final = "converting"
PREVIEW_READY: Final = "preview_ready"
WAITING_USER_CONFIRM: Final = "waiting_user_confirm"
QUEUED_FOR_PRINT: Final = "queued_for_print"
PRINTING: Final = "printing"
SUBMITTED_TO_CUPS: Final = "submitted_to_cups"
PRINTED: Final = "printed"
CONVERSION_FAILED: Final = "conversion_failed"
PRINT_FAILED: Final = "print_failed"
PRINT_STATUS_UNKNOWN: Final = "print_status_unknown"
CANCELLED: Final = "cancelled"
MANUAL_REQUIRED: Final = "manual_required"
MANUAL_COMPLETED: Final = "manual_completed"
EXPIRED: Final = "expired"

TERMINAL_STATES: Final = frozenset({PRINTED, MANUAL_COMPLETED, CANCELLED, EXPIRED})
HISTORY_STATES: Final = TERMINAL_STATES
SUCCESS_CLEANUP_STATES: Final = frozenset({PRINTED, MANUAL_COMPLETED})
FAILED_RETAIN_STATES: Final = frozenset({CONVERSION_FAILED, PRINT_FAILED, PRINT_STATUS_UNKNOWN, MANUAL_REQUIRED})

LEASEABLE_STATES: Final = {
    QUEUED_FOR_CONVERSION: "conversion",
    QUEUED_FOR_PRINT: "print",
}


def requires_success_cleanup(status: str) -> bool:
    return status in SUCCESS_CLEANUP_STATES
