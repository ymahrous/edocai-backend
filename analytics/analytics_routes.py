import database, models
from sqlmodel import Session, select
from datetime import datetime, timezone
from dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

def get_amount(extracted_data: dict) -> float:
    try:
        amount_str = extracted_data.get("total_amount", "0")
        if isinstance(amount_str, (int, float)): return float(amount_str)
        return float(str(amount_str).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0

def base_query(user_id: str, session: Session, year: int, month: int = None):
    """Helper to build the base query with date filtering"""
    stmt = (
        select(models.Extraction, models.Document)
        .join(models.Document, models.Extraction.document_id == models.Document.id)
        .where(models.Document.owner_id == user_id)
        .where(models.Document.status == "COMPLETED")
    )
    
    # Filter by Year (Required)
    start_of_year = datetime(year, 1, 1, tzinfo=timezone.utc)
    end_of_year = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    stmt = stmt.where(models.Document.created_at >= start_of_year, models.Document.created_at < end_of_year)
    
    # Filter by Month (Optional)
    if month and 1 <= month <= 12:
        start_of_month = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end_of_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_of_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        stmt = stmt.where(models.Document.created_at >= start_of_month, models.Document.created_at < end_of_month)
        
    return session.exec(stmt).all()

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
        month_key = doc.created_at.strftime("%Y-%m")
        month_map[month_key] = month_map.get(month_key, 0) + get_amount(ext.extracted_data)

    sorted_months = sorted(month_map.items())
    return [{"month": k, "spend": round(v, 2)} for k, v in sorted_months]