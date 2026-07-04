import re
import io
import csv
import structlog
import storage_client
import database, models
from sqlalchemy import delete
from sqlmodel import Session, select
from tasks import process_document_task
from datetime import datetime, timezone
from dependencies import get_current_user, increment_usage
from fastapi import APIRouter, Depends, HTTPException, Request, Header, status, UploadFile, File

router = APIRouter(prefix="/api/v1", tags=["document"])

@router.post("/upload/")
def test_upload(
    file: UploadFile = File(...), 
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    # --- FREE TIER LIMIT ENFORCEMENT ---
    FREE_TIER_LIMIT = 10
    if current_user.plan == "free":
        current_month = datetime.now(timezone.utc).replace(day=1)
        usage = session.exec(
            select(models.UsageRecord)
            .where(models.UsageRecord.user_id == current_user.id)
            .where(models.UsageRecord.month == current_month)
        ).first()
        
        docs_processed = usage.documents_processed if usage else 0
        if docs_processed >= FREE_TIER_LIMIT:
            raise HTTPException(
                status_code=403, 
                detail={
                    "error": "limit_exceeded", 
                    "message": "Free tier limit reached. Upgrade to Pro for unlimited uploads.",
                    "limit": FREE_TIER_LIMIT
                }
            )

    file_bytes = file.file.read()
    filename = file.filename
    public_url = storage_client.upload_to_storage(file_bytes, filename)
    
    db_doc = models.Document(
        filename=filename,
        s3_url=public_url,
        status="PENDING",
        owner_id=current_user.id
    )
    session.add(db_doc)
    session.commit()
    session.refresh(db_doc)
    
    process_document_task.delay(db_doc.id)
    
    # NEW: Increment usage after successful upload dispatch
    increment_usage(current_user.id)

    return {
        "message": "Document received!",
        "document_id": db_doc.id,
        "status": db_doc.status
    }

@router.get("/documents/")
def get_documents(
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    # Only select documents where owner_id matches the logged-in user
    docs = session.exec(
        select(models.Document)
        .where(models.Document.owner_id == current_user.id)
        .order_by(models.Document.created_at.desc())
    ).all()
    return docs


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    document = session.exec(
        select(models.Document).where(models.Document.id == document_id)
    ).first()

    if not document or document.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    session.exec(
        delete(models.Extraction).where(models.Extraction.document_id == document_id)
    )
    session.flush()

    session.delete(document)
    session.commit()

    storage_client.delete_from_storage(document.filename)

    return None

@router.get("/extraction/{document_id}")
def get_extraction(
    document_id: str, 
    session: Session = Depends(database.get_session),
    current_user: str = Depends(get_current_user)
):
    extraction = session.exec(
        select(models.Extraction).where(models.Extraction.document_id == document_id)
    ).first()
    
    if not extraction:
        raise HTTPException(status_code=404, detail="Extraction not found or still processing.")
        
    return {
        "document_id": extraction.document_id,
        "extracted_data": extraction.extracted_data,
        "confidence_score": extraction.confidence_score,
        "category": extraction.category if extraction.category else "Other"
    }

@router.patch("/api/v1/extraction/{document_id}/category")
def update_category(
    document_id: str, 
    category_update: dict, 
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    # Pro Gate
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Tax categorization is a Pro feature.")
        
    extraction = session.exec(
        select(models.Extraction).where(models.Extraction.document_id == document_id)
    ).first()
    
    if not extraction:
        raise HTTPException(status_code=404, detail="Extraction not found.")
        
    # Validate category against our standard list
    valid_categories = ["Travel", "Meals", "Software", "Office Supplies", "Equipment", "Marketing", "Utilities", "Rent", "Insurance", "Professional Services", "Other"]
    new_category = category_update.get("category")
    
    if new_category not in valid_categories:
        raise HTTPException(status_code=400, detail=f"Invalid category. Must be one of: {valid_categories}")
        
    extraction.category = new_category
    session.add(extraction)
    session.commit()
    
    return {"message": "Category updated", "category": new_category}


# --- TAX SUMMARY EXPORT ---
@router.get("/api/v1/reports/tax-summary/")
def get_tax_summary(
    year: int, 
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    # Pro Gate
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="Tax export is a Pro feature.")

    # Get all completed documents for the user in the given year that have a category
    docs = session.exec(
        select(models.Document, models.Extraction)
        .join(models.Extraction, models.Document.id == models.Extraction.document_id)
        .where(models.Document.owner_id == current_user.id)
        .where(models.Document.status == "COMPLETED")
        .where(models.Extraction.category != None)
    ).all()

    # Aggregate data by category
    summary = {}
    for doc, ext in docs:
        # Parse the date to check the year
        raw_date = str(ext.extracted_data.get("date", ""))
        try:
            doc_year = int(raw_date.split("-")[0])
        except:
            continue # Skip if date is unparseable
            
        if doc_year == year:
            cat = ext.category
            raw_amount = str(ext.extracted_data.get("total_amount", "0"))
            clean_amount = re.sub(r'[^\d\.-]', '', raw_amount)
            amount = float(clean_amount) if clean_amount else 0.0
            
            summary[cat] = round(summary.get(cat, 0.0) + amount, 2)

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Category", f"Total Spend ({year})"])
    for cat, total in sorted(summary.items()):
        writer.writerow([cat, f"${total:,.2f}"])
    
    output.seek(0)
    
    return StreamingResponse(
        output, 
        media_type="text/csv", 
        headers={"Content-Disposition": f"attachment; filename=edocAI_Tax_Summary_{year}.csv"}
    )