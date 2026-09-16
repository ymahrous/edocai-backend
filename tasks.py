import ai_extractor
from database import engine
from typing import List
from sqlmodel import Session
from celery_app import celery_app
from models import Document, Extraction
from detection import check_for_duplicates
from vendor.vendor_utils import match_or_create_vendor
import httpx
import os
import re
import asyncio

# Currency conversion setup
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")
EXCHANGE_RATE_BASE_URL = "https://v6.exchangerate-api.com/v6"

async def convert_currency(amount: float, from_currency: str, to_currency: str) -> tuple[float, float]:
    """
    Convert amount from one currency to another.
    Returns: (converted_amount, exchange_rate)
    """
    if from_currency == to_currency:
        return amount, 1.0

    if not EXCHANGE_RATE_API_KEY:
        # Fallback: return original amount if no API key
        print("⚠️ No EXCHANGE_RATE_API_KEY set, skipping conversion")
        return amount, 1.0

    url = f"{EXCHANGE_RATE_BASE_URL}/{EXCHANGE_RATE_API_KEY}/pair/{from_currency}/{to_currency}/{amount}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            if data.get("result") == "success":
                converted = data.get("conversion_result", amount)
                rate = data.get("conversion_rate", 1.0)
                return float(converted), float(rate)
            else:
                print(f"⚠️ Exchange rate API error: {data.get('error-type', 'unknown')}")
                return amount, 1.0
        except Exception as e:
            print(f"⚠️ Currency conversion failed: {e}")
            return amount, 1.0


@celery_app.task
def process_document_task(document_id: str):
    print(f"🔥 Celery received job for document: {document_id}")

    with Session(engine) as session:
        document = session.get(Document, document_id)
        if not document:
            return {"error": "Document not found"}

        # Get user's base currency
        from models import User
        user = session.get(User, document.owner_id)
        base_currency = user.base_currency if user else "USD"

        try:
            document.status = "PROCESSING"
            session.add(document)
            session.commit()

            ai_result = ai_extractor.run_ai_extraction(document.s3_url)

            extracted_data = ai_result["data"]
            confidence = ai_result["confidence"]
            category = extracted_data.get("category", "Other")

            # NEW: Extract currency info
            raw_amount_str = extracted_data.get("total_amount", "0")
            original_currency = extracted_data.get("currency", "USD").upper()

            # Parse amount from string (remove symbols, commas)
            clean_amount = re.sub(r'[^\d\.-]', '', raw_amount_str)
            original_amount = float(clean_amount) if clean_amount else 0.0

            # NEW: Convert to user's base currency
            # Note: Celery tasks are sync, so we run async conversion in event loop
            converted_amount, exchange_rate = asyncio.run(
                convert_currency(original_amount, original_currency, base_currency)
            )

            # --- NEW: Vendor Intelligence Matching ---
            vendor_id = None
            raw_vendor_name = extracted_data.get("vendor") # Adjust this key if your Gemini prompt outputs something like "vendor_name"

            if raw_vendor_name:
                # This will fuzzy match an existing vendor OR create a new one automatically
                vendor = match_or_create_vendor(session, document.owner_id, raw_vendor_name)
                if vendor:
                    vendor_id = vendor.id
            # ----------------------------------------

            extraction = Extraction(
                document_id=document_id,
                extracted_data=extracted_data,
                confidence_score=confidence,
                category=category,
                vendor_id=vendor_id, # NEW: Save the matched vendor ID
                # NEW: Store currency fields
                original_currency=original_currency,
                original_amount=original_amount,
                converted_amount=round(converted_amount, 2),
                exchange_rate=round(exchange_rate, 6)
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