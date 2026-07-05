import ai_extractor
from database import engine
from sqlmodel import Session
from celery_app import celery_app
from models import Document, Extraction
from detection import check_for_duplicates

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

            ai_result = ai_extractor.run_ai_extraction(document.s3_url)
            
            extracted_data = ai_result["data"]
            confidence = ai_result["confidence"]
            category = extracted_data.get("category", "Other")

            extraction = Extraction(
                document_id=document_id,
                extracted_data=extracted_data,
                confidence_score=confidence,
                category=category
            )
            session.add(extraction)
            
            document.status = "COMPLETED"
            session.add(document)
            session.commit()

            if extracted_data.get("vendor"):
                flags = check_for_duplicates(
                    current_user_id=document.owner_id,
                    extraction_data=extracted_data,
                    current_doc_id=document.id,
                    session=session
                )
                
                if flags:
                    document.flags = ",".join(flags)
                    session.add(document)
                    session.commit()

            print(f"✅ Successfully processed document: {document_id}")
            return {"status": "success", "document_id": document_id}

        except Exception as e:
            document.status = "FAILED"
            session.add(document)
            session.commit()
            print(f"❌ Failed to process document: {e}")
            return {"status": "failed", "error": str(e)}