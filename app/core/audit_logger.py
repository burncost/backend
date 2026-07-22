"""
Audit logger for tracking business-critical actions.
Writes to a separate audit log file for compliance and debugging.
"""
import logging
import logging.handlers
import json
import os
from datetime import datetime
from typing import Optional

audit_logger = logging.getLogger("audit")


def setup_audit_logging(log_dir: str = "logs"):
    """Configure audit logging."""
    os.makedirs(log_dir, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "burncost_audit.log"),
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=20,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(handler)
    audit_logger.setLevel(logging.INFO)


def log_audit_event(
    action: str,
    user_id: Optional[str] = None,
    vendor_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
):
    """
    Log a business-critical audit event.

    Examples:
        log_audit_event("user.registered", user_id="...", details={"email": "..."})
        log_audit_event("vendor.onboarded", user_id="...", vendor_id="...")
        log_audit_event("order.created", user_id="...", resource_id="ORD-123")
        log_audit_event("product.updated", vendor_id="...", resource_id="prod-uuid")
        log_audit_event("payment.processed", user_id="...", details={"amount": 50000})
    """
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "user_id": user_id or "-",
        "vendor_id": vendor_id or "-",
        "resource_type": resource_type or "-",
        "resource_id": resource_id or "-",
        "ip_address": ip_address or "-",
        "details": details or {},
    }
    audit_logger.info(json.dumps(entry, default=str))