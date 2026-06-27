# main.py
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException
from sqlmodel import Session, select
import database, models
from sqlmodel import select
import storage_client
from tasks import process_document_task
from auth_routes import router as auth_router
from dependencies import get_current_user
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="ExtractIQ API")

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

app.include_router(auth_router)

# --- PROTECTED ROUTES ---
@app.post("/api/v1/upload/")
def test_upload(
    file: UploadFile = File(...), 
    session: Session = Depends(database.get_session),
    current_user: str = Depends(get_current_user)
):
    file_bytes = file.file.read()
    filename = file.filename
    public_url = storage_client.upload_to_storage(file_bytes, filename)
    
    db_doc = models.Document(
        filename=filename,
        s3_url=public_url,
        status="PENDING"
    )
    session.add(db_doc)
    session.commit()
    session.refresh(db_doc)
    
    process_document_task.delay(db_doc.id)
    
    return {
        "message": "Document received and sent to ML queue!",
        "document_id": db_doc.id,
        "status": db_doc.status
    }

@app.get("/api/v1/documents/")
def get_documents(
    session: Session = Depends(database.get_session),
    current_user: str = Depends(get_current_user)
):
    docs = session.exec(
        select(models.Document).order_by(models.Document.created_at.desc())
    ).all()
    return docs

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