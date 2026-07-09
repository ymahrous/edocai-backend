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
    """Tries to parse the invoice date from JSON. Falls back to upload date."""
    raw_date = extracted_data.get("date")
    if not raw_date:
        return None
    
    if isinstance(raw_date, datetime):
        return raw_date.replace(tzinfo=timezone.utc)
        
    # Try common formats: YYYY-MM-DD, MM/DD/YYYY, etc.
    formats_to_try = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]
    if isinstance(raw_date, str):
        # Strip time components if they exist
        clean_date_str = raw_date.split("T")[0].split(" ")[0]
        
        for fmt in formats_to_try:
            try:
                dt = datetime.strptime(clean_date_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None

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

    # 3. Current Month Spend (Based on INVOICE date)
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Fetch all completed extractions for the user created this month (to limit query size)
    extractions = session.exec(
        select(models.Extraction, models.Document)
        .join(models.Document, models.Extraction.document_id == models.Document.id)
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
        # Optimization: Only look at documents uploaded in the last 6 months 
        # to catch late-uploaded invoices without scanning the entire DB
        .where(models.Document.created_at >= start_of_month.replace(month=now.month - 5 if now.month > 5 else 1, year=now.year - 1 if now.month <= 5 else now.year))
    ).all()

    month_spend = 0.0
    for ext, doc in extractions:
        # Parse the invoice date from the extracted JSON
        inv_date = parse_invoice_date(ext.extracted_data)
        
        # Use the invoice date if found, otherwise fall back to the document upload date
        check_date = inv_date if inv_date else doc.created_at
        
        # If the invoice date falls within the current month, add the amount
        if check_date >= start_of_month and check_date <= now:
            month_spend += get_amount(ext.extracted_data)

    return {
        "processed": processed_count,
        "synced": synced_count,
        "month_spend": round(month_spend, 2)
    }