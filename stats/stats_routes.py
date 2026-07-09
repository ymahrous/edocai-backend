import re
import database, models
from fastapi import APIRouter, Depends
from datetime import datetime, timezone
from dependencies import get_current_user
from sqlmodel import Session, select, func

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])

def get_amount(extracted_data: dict) -> float:
    try:
        raw_val = extracted_data.get("total_amount", "0")
        if isinstance(raw_val, (int, float)): return float(raw_val)
        if isinstance(raw_val, str):
            cleaned = re.sub(r"[^\d.]", "", raw_val)
            try: return float(cleaned)
            except ValueError: return 0.0
        return 0.0
    except: return 0.0

def parse_invoice_date(extracted_data: dict) -> datetime:
    raw_date = extracted_data.get("date")
    if not raw_date: return None
    if isinstance(raw_date, datetime): return raw_date.replace(tzinfo=timezone.utc)
    formats_to_try = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]
    if isinstance(raw_date, str):
        clean_date_str = raw_date.split("T")[0].split(" ")[0]
        for fmt in formats_to_try:
            try:
                dt = datetime.strptime(clean_date_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError: continue
    return None

@router.get("/dashboard")
def get_dashboard_stats(
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Total Processed Documents (From IMMUTABLE usage ledger)
    # This sums up every increment_usage() ever called for this user.
    total_processed = session.exec(
        select(func.sum(models.UsageRecord.documents_processed))
        .where(models.UsageRecord.user_id == current_user.id)
    ).one() or 0

    # 2. Total Synced Documents (Still counts live documents, as deletion removes sync state)
    synced_count = session.exec(
        select(func.count(models.Document.id))
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.quickbooks_synced == True)
    ).one()

    # 3. Current Month Spend
    extractions = session.exec(
        select(models.Extraction, models.Document)
        .join(models.Document, models.Extraction.document_id == models.Document.id)
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
    ).all()

    month_spend = 0.0
    for ext, doc in extractions:
        inv_date = parse_invoice_date(ext.extracted_data)
        check_date = inv_date if inv_date else doc.created_at
        
        if check_date >= start_of_month and check_date <= now:
            month_spend += get_amount(ext.extracted_data)

    return {
        "processed": total_processed, # Now immune to deletions!
        "synced": synced_count,
        "month_spend": round(month_spend, 2)
    }