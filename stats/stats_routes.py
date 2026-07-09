import re
import database, models
from fastapi import APIRouter, Depends
from datetime import datetime, timezone
from dependencies import get_current_user
from sqlmodel import Session, select, func, col

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

@router.get("/dashboard")
def get_dashboard_stats(
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    # 1. Total Processed Documents (COMPLETED status)
    processed_count = session.exec(
        select(func.count(models.Document.id))
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
    ).one()

    # 2. Total Synced Documents
    synced_count = session.exec(
        select(func.count(models.Document.id))
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.quickbooks_synced == True)
    ).one()

    # 3. Current Month Spend
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    extractions = session.exec(
        select(models.Extraction, models.Document)
        .join(models.Document, models.Extraction.document_id == models.Document.id)
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
        .where(models.Document.created_at >= start_of_month)
    ).all()

    month_spend = sum(get_amount(ext.extracted_data) for ext, doc in extractions)

    return {
        "processed": processed_count,
        "synced": synced_count,
        "month_spend": round(month_spend, 2)
    }