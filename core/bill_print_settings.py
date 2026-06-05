"""Bill print settings — re-exports from bill_config for template compatibility."""
from core.bill_config import (
    DEFAULT_BILL_PRINT_SETTINGS,
    load_bill_print_settings,
    save_bill_print_settings,
)

__all__ = [
    "DEFAULT_BILL_PRINT_SETTINGS",
    "load_bill_print_settings",
    "save_bill_print_settings",
]
