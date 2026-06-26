# tasks.py
import time
from sqlmodel import Session, select
from database import engine
from models import Document, Extraction
from celery_app import celery_app
import ai_extractor # <--- IMPORT OUR NEW AI MODULE

@celery_app.task
def process_document_task(document_id: str):
    print(f"🔥 Celery received job for document: {document_id}")
    
    with Session(engine) as session:
        document = session.get(Document, document_id)
        if not document:
            return {"error": "Document not found"}
        
        try:
            document.status = "PROCESSING"
            session.add(document)
            session.commit()

            # ==========================================
            # THE NEW AI PIPELINE CALL
            # ==========================================
            ai_result = ai_extractor.run_ai_extraction(document.s3_url)
            
            extracted_data = ai_result["data"]
            confidence = ai_result["confidence"]

            # Save to DB
            extraction = Extraction(
                document_id=document_id,
                extracted_data=extracted_data,
                confidence_score=confidence
            )
            session.add(extraction)
            
            document.status = "COMPLETED"
            session.add(document)
            session.commit()

            print(f"✅ Successfully processed document: {document_id} via {ai_result['source']}")
            return {"status": "success", "document_id": document_id}

        except Exception as e:
            document.status = "FAILED"
            session.add(document)
            session.commit()
            print(f"❌ Failed to process document: {e}")
            return {"status": "failed", "error": str(e)}