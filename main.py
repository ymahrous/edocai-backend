import structlog
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status
from sqlmodel import Session, select
from sqlalchemy import delete
import database, models
from sqlmodel import select
import storage_client
from tasks import process_document_task
from auth_routes import router as auth_router
from dependencies import get_current_user, increment_usage
from fastapi.middleware.cors import CORSMiddleware
from billing_routes import router as billing_router
from datetime import datetime, timezone

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer()
    ])
logger = structlog.get_logger("edocai.api")

app = FastAPI(title="edocAI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
    expose_headers=["*"]
)

@app.on_event("startup")
def on_startup():
    database.init_db()
    logger.info("edocAI API started successfully.")

app.include_router(auth_router)
app.include_router(billing_router)

# UPDATED MAIN ROUTE
@app.post("/api/v1/upload/")
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

@app.get("/api/v1/documents/")
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


@app.delete("/api/v1/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
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

@app.get("/api/v1/extraction/{document_id}")
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
        "confidence_score": extraction.confidence_score
    }