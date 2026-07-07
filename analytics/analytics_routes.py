import database, models
from datetime import datetime, timezone
from dependencies import get_current_user
from sqlmodel import Session, select, func, col
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

# Helper to safely parse amounts from the JSON column
def get_amount(extracted_data: dict) -> float:
    try:
        amount_str = extracted_data.get("total_amount", "0")
        if isinstance(amount_str, (int, float)): return float(amount_str)
        return float(str(amount_str).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0

@router.get("/spend-by-category")
def get_spend_by_category(
    current_user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session)
):
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Analytics require Pro plan")

    extractions = session.exec(
        select(models.Extraction, models.Document)
        .join(models.Document, models.Extraction.document_id == models.Document.id)
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
    ).all()

    spend_map = {}
    for ext, doc in extractions:
        category = ext.category or "Uncategorized"
        spend_map[category] = spend_map.get(category, 0) + get_amount(ext.extracted_data)

    return [{"name": k, "value": round(v, 2)} for k, v in spend_map.items()]

@router.get("/spend-by-vendor")
def get_spend_by_vendor(
    current_user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session)
):
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Analytics require Pro plan")

    extractions = session.exec(
        select(models.Extraction, models.Document)
        .join(models.Document, models.Extraction.document_id == models.Document.id)
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
    ).all()

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
    current_user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session)
):
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Analytics require Pro plan")

    extractions = session.exec(
        select(models.Extraction, models.Document)
        .join(models.Document, models.Extraction.document_id == models.Document.id)
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
    ).all()

    month_map = {}
    for ext, doc in extractions:
        # Format to "YYYY-MM"
        month_key = doc.created_at.strftime("%Y-%m")
        month_map[month_key] = month_map.get(month_key, 0) + get_amount(ext.extracted_data)

    sorted_months = sorted(month_map.items())
    return [{"month": k, "spend": round(v, 2)} for k, v in sorted_months]