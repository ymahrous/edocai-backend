from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from datetime import datetime, timezone
import database, models
from dependencies import get_current_user
import re

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

def get_amount(extracted_data: dict) -> float:
    raw_val = extracted_data.get("total_amount", "0")
    if isinstance(raw_val, (int, float)): return float(raw_val)
    if isinstance(raw_val, str):
        cleaned = re.sub(r"[^\d.]", "", raw_val)
        try: return float(cleaned)
        except ValueError: return 0.0
    return 0.0

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
        # Strip time components if they exist (e.g., "2023-10-27T00:00:00")
        clean_date_str = raw_date.split("T")[0].split(" ")[0]
        
        for fmt in formats_to_try:
            try:
                dt = datetime.strptime(clean_date_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None

def base_query(user_id: str, session: Session, year: int, month: int = None):
    """Helper to build the base query filtering by INVOICE date"""
    stmt = (
        select(models.Extraction, models.Document)
        .join(models.Document, models.Extraction.document_id == models.Document.id)
        .where(models.Document.owner_id == user_id)
        .where(models.Document.status == "COMPLETED")
    )
    
    # Fetch results and filter by parsed invoice date in Python 
    # (SQLalchemy JSON querying across SQLite/Postgres is unreliable, so we do it in memory)
    results = session.exec(stmt).all()
    filtered_results = []
    
    for ext, doc in results:
        inv_date = parse_invoice_date(ext.extracted_data)
        
        # If no invoice date, fall back to the document upload date
        check_date = inv_date if inv_date else doc.created_at
        
        if check_date.year == year:
            if month is None or check_date.month == month:
                filtered_results.append((ext, doc))
                
    return filtered_results

@router.get("/spend-by-category")
def get_spend_by_category(
    year: int = Query(..., description="Year to filter by"),
    month: int = Query(None, description="Optional month (1-12) to filter by"),
    current_user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session)
):
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Analytics require Pro plan")

    extractions = base_query(current_user.id, session, year, month)
    spend_map = {}
    for ext, doc in extractions:
        category = ext.category or "Uncategorized"
        spend_map[category] = spend_map.get(category, 0) + get_amount(ext.extracted_data)

    return [{"name": k, "value": round(v, 2)} for k, v in spend_map.items()]

@router.get("/spend-by-vendor")
def get_spend_by_vendor(
    year: int = Query(..., description="Year to filter by"),
    month: int = Query(None, description="Optional month (1-12) to filter by"),
    current_user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session)
):
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Analytics require Pro plan")

    extractions = base_query(current_user.id, session, year, month)
    vendor_map = {}
    for ext, doc in extractions:
        vendor_name = "Unknown"
        if ext.vendor_id:
            vendor = session.get(models.Vendor, ext.vendor_id)
            if vendor: vendor_name = vendor.canonical_name
        vendor_map[vendor_name] = vendor_map.get(vendor_name, 0) + get_amount(ext.extracted_data)

    sorted_vendors = sorted(vendor_map.items(), key=lambda item: item[1], reverse=True)[:10]
    return [{"name": k, "value": round(v, 2)} for k, v in sorted_vendors]

@router.get("/monthly-trend")
def get_monthly_trend(
    year: int = Query(..., description="Year to filter by"),
    current_user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session)
):
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Analytics require Pro plan")

    extractions = base_query(current_user.id, session, year)
    month_map = {}
    for ext, doc in extractions:
        inv_date = parse_invoice_date(ext.extracted_data)
        check_date = inv_date if inv_date else doc.created_at
        
        month_key = check_date.strftime("%Y-%m")
        month_map[month_key] = month_map.get(month_key, 0) + get_amount(ext.extracted_data)

    sorted_months = sorted(month_map.items())
    return [{"month": k, "spend": round(v, 2)} for k, v in sorted_months]