import re
import models
from sqlmodel import Session, select
from datetime import datetime, timedelta

def check_for_duplicates(current_user_id: str, extraction_data: dict, current_doc_id: str, session: Session) -> list[str]:
    flags = []
    
    vendor = extraction_data.get("vendor", "")
    raw_amount = str(extraction_data.get("total_amount", "0"))
    clean_amount = re.sub(r'[^\d\.-]', '', raw_amount)
    
    try:
        amount = float(clean_amount)
    except ValueError:
        amount = 0.0

    if not vendor or amount == 0.0:
        return flags

    # 1. DUPLICATE CHECK: Same vendor, same amount, within the last 30 days
    thirty_days_ago = datetime.now() - timedelta(days=30)
    
    existing_docs = session.exec(
        select(models.Document, models.Extraction)
        .join(models.Extraction, models.Document.id == models.Extraction.document_id)
        .where(models.Document.owner_id == current_user_id)
        .where(models.Document.id != current_doc_id) # Don't compare against itself
        .where(models.Document.status == "COMPLETED")
        .where(models.Document.created_at >= thirty_days_ago)
    ).all()

    for doc, ext in existing_docs:
        ext_vendor = str(ext.extracted_data.get("vendor", "")).lower()
        ext_raw_amount = str(ext.extracted_data.get("total_amount", "0"))
        ext_clean_amount = re.sub(r'[^\d\.-]', '', ext_raw_amount)
        
        try:
            ext_amount = float(ext_clean_amount)
        except ValueError:
            ext_amount = 0.0

        # DEBUG PRINTS
        print(f"--- Comparing Docs ---")
        print(f"Current: Vendor='{vendor.lower()}', Amount={amount}")
        print(f"Existing: Vendor='{ext_vendor}', Amount={ext_amount}")

        # Fuzzy match vendor (one contains the other) and exact match amount
        vendor_match = (vendor.lower() in ext_vendor or ext_vendor in vendor.lower())
        amount_match = (amount == ext_amount)

        print(f"Vendor Match: {vendor_match} | Amount Match: {amount_match}")

        if vendor_match and amount_match:
            flags.append("possible_duplicate")
            break 

    # 2. ANOMALY CHECK: Amount is 3x higher than the historical average for this vendor
    all_vendor_docs = session.exec(
        select(models.Extraction)
        .join(models.Document, models.Document.id == models.Extraction.document_id)
        .where(models.Document.owner_id == current_user_id)
        .where(models.Document.id != current_doc_id)
        .where(models.Document.status == "COMPLETED")
    ).all()

    vendor_amounts = []
    for ext in all_vendor_docs:
        ext_vendor = str(ext.extracted_data.get("vendor", "")).lower()
        if vendor.lower() in ext_vendor or ext_vendor in vendor.lower():
            ext_raw = str(ext.extracted_data.get("total_amount", "0"))
            ext_clean = re.sub(r'[^\d\.-]', '', ext_raw)
            try:
                vendor_amounts.append(float(ext_clean))
            except ValueError:
                pass

    if len(vendor_amounts) >= 2: # Need at least 2 past purchases to establish a pattern
        avg_amount = sum(vendor_amounts) / len(vendor_amounts)
        if avg_amount > 0 and amount > (avg_amount * 3):
            flags.append("price_anomaly")

    return flags