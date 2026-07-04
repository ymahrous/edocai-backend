import re
import os
import httpx
import database, models
from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv
from sqlmodel import Session, select
from datetime import datetime, timezone
from dependencies import get_current_user
from fastapi.responses import RedirectResponse
from fastapi import APIRouter, Depends, HTTPException, Request, Query

load_dotenv()

router = APIRouter(prefix="/api/v1/quickbooks", tags=["quickbooks"])

class QBCallbackBody(BaseModel):
    realm_id: Optional[str] = None

# QuickBooks Environment Variables
QB_CLIENT_ID = os.getenv("QB_CLIENT_ID")
QB_CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET")
QB_ENVIRONMENT = os.getenv("QB_ENVIRONMENT", "sandbox") # or "production"
QB_REDIRECT_URI = os.getenv("QB_REDIRECT_URI")

# QuickBooks Auth URLs
AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
if QB_ENVIRONMENT == "sandbox":
    API_BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
else:
    API_BASE_URL = "https://quickbooks.api.intuit.com"


# --- 1. OAUTH FLOW ---

@router.get("/connect")
def connect_quickbooks(current_user: models.User = Depends(get_current_user)):
    """Generates the Intuit authorization URL for the frontend to redirect to."""
    if not QB_CLIENT_ID or not QB_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="QuickBooks integration not configured on server.")

    # Scope: com.intuit.quickbooks.accounting to create expenses
    scope = "com.intuit.quickbooks.accounting"
    auth_request_url = (
        f"{AUTH_URL}?client_id={QB_CLIENT_ID}&scope={scope}&redirect_uri={QB_REDIRECT_URI}"
        f"&response_type=code&state={current_user.id}" # Pass user ID in state to verify on callback
    )
    return {"url": auth_request_url}

@router.get("/callback")
async def quickbooks_callback(
    request: Request,
    code: str, 
    state: str, 
    realm_id: str = Query(None, alias="realmId")
):

    print(request.url)
    print(dict(request.query_params))
    # If realm_id wasn't in the URL, try to read it from the POST body
    if not realm_id:
        try:
            body = await request.json()
            realm_id = body.get("realm_id")
        except:
            pass

    # If we STILL don't have a realm_id, redirect with error
    if not realm_id:
        return RedirectResponse(url=f"{os.getenv('FRONTEND_URL')}/account?qb_error=missing_realm")

    # Trade the auth code for tokens
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": QB_REDIRECT_URI,
    }

    response = httpx.post(
        TOKEN_URL,
        data=data,
        headers=headers,
        auth=(QB_CLIENT_ID, QB_CLIENT_SECRET),
    )

    if response.status_code != 200:
        print("QB Token Error:", response.text)
        return RedirectResponse(url=f"{os.getenv('FRONTEND_URL')}/account?qb_error=token_failed")

    token_data = response.json()
    user_id = state 

    with next(database.get_session()) as session:
        # Upsert the connection
        qb_conn = session.exec(
            select(models.QuickBooksConnection).where(models.QuickBooksConnection.user_id == user_id)
        ).first()

        if not qb_conn:
            qb_conn = models.QuickBooksConnection(user_id=user_id, realm_id=realm_id)
        
        qb_conn.access_token = token_data["access_token"]
        qb_conn.refresh_token = token_data["refresh_token"]
        qb_conn.updated_at = datetime.now(timezone.utc)
        
        session.add(qb_conn)
        session.commit()

    # REDIRECT BACK TO FRONTEND with success parameter
    return RedirectResponse(url=f"{os.getenv('FRONTEND_URL')}/account?qb_success=true")

def refresh_qb_token(qb_conn: models.QuickBooksConnection, session: Session):
    """Helper to refresh an expired access token using the refresh token."""
    headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "refresh_token", "refresh_token": qb_conn.refresh_token}

    response = httpx.post(TOKEN_URL, data=data, headers=headers, auth=(QB_CLIENT_ID, QB_CLIENT_SECRET))

    if response.status_code != 200:
        # Refresh token is invalid, user must re-auth
        session.delete(qb_conn)
        session.commit()
        raise HTTPException(status_code=401, detail="QuickBooks connection expired. Please reconnect.")

    token_data = response.json()
    qb_conn.access_token = token_data["access_token"]
    qb_conn.refresh_token = token_data["refresh_token"]
    qb_conn.updated_at = datetime.now(timezone.utc)
    session.add(qb_conn)
    session.commit()
    return qb_conn


# --- 2. SYNC TO QUICKBOOKS ---

@router.post("/sync/{document_id}")
def sync_to_quickbooks(
    document_id: str,
    current_user: models.User = Depends(get_current_user)
):
    # GATE: Pro Feature
    if current_user.plan != "pro":
        raise HTTPException(status_code=403, detail="QuickBooks sync is a Pro feature. Please upgrade.")

    with next(database.get_session()) as session:
        # Verify document belongs to user
        doc = session.exec(
            select(models.Document).where(models.Document.id == document_id, models.Document.owner_id == current_user.id)
        ).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found.")

        extraction = session.exec(
            select(models.Extraction).where(models.Extraction.document_id == document_id)
        ).first()
        if not extraction:
            raise HTTPException(status_code=404, detail="Extraction not ready.")

        # Get QB Connection
        qb_conn = session.exec(
            select(models.QuickBooksConnection).where(models.QuickBooksConnection.user_id == current_user.id)
        ).first()
        if not qb_conn:
            raise HTTPException(status_code=400, detail="QuickBooks not connected. Please connect in settings.")

        # Prepare the QuickBooks Purchase (Expense) payload
        extracted = extraction.extracted_data
        vendor_name = extracted.get("vendor", "Unknown Vendor")
        raw_amount = str(extracted.get("total_amount", "0"))
        raw_date = str(extracted.get("date", ""))
        
        # --- 1. ROBUST AMOUNT PARSING ---
        # Strip everything except numbers, decimals, and minus signs
        clean_amount = re.sub(r'[^\d\.-]', '', raw_amount)
        try:
            total_val = float(clean_amount)
        except ValueError:
            total_val = 0.0

        # --- 2. ROBUST DATE PARSING ---
        qb_date = None
        try:
            # Try to parse standard formats AI might return (e.g., YYYY-MM-DD, MM/DD/YYYY, Month DD, YYYY)
            # If AI returns YYYY-MM-DD, this handles it.
            if "-" in raw_date and len(raw_date) == 10:
                qb_date = raw_date
            else:
                # Fallback: Try to parse with Python datetime, then convert back to YYYY-MM-DD
                parsed_date = datetime.strptime(raw_date, "%B %d, %Y") # Adjust this mask if AI uses a different format
                qb_date = parsed_date.strftime("%Y-%m-%d")
        except:
            pass # If parsing fails, qb_date remains None, and QB will default to today

        # --- 3. DETERMINE PAYMENT TYPE ---
        payment_type = "Cash"
        doc_string = str(extracted).lower()
        if any(word in doc_string for word in ["mastercard", "visa", "credit card", "amex"]):
            payment_type = "CreditCard"

        # --- 4. MAP CATEGORIES TO QUICKBOOKS ACCOUNT IDS ---
        # QuickBooks requires an AccountRef ID. These are standard default IDs in QBO.
        # In a perfect world (Phase 13), we query the user's chart of accounts. 
        # For now, we guess based on the category.
        category = str(extracted.get("category", "")).lower()
        account_id = "41" # Default: Opening Balance Equity
        account_name = "Opening Balance Equity"
        
        if "travel" in category or "airline" in category or "hotel" in category:
            account_id = "13" 
            account_name = "Travel"
        elif "meal" in category or "food" in category or "restaurant" in category:
            account_id = "24"
            account_name = "Meals and Entertainment"
        elif "software" in category or "subscription" in category or "saas" in category:
            account_id = "56"
            account_name = "Software and Subscriptions"
        elif "office" in category or "equipment" in category or "supplies" in category:
            account_id = "28"
            account_name = "Office Supplies"

        # --- 5. BUILD THE BULLETPROOF QB PAYLOAD ---
        qb_payload = {
            "PaymentType": payment_type,
            "AccountRef": {"value": "41", "name": "Opening Balance Equity"}, # The funding account
            "TotalAmt": total_val,
            "EntityRef": {"value": "1", "name": vendor_name, "type": "Vendor"}, # TYPE IS CRITICAL FOR PAYEE
            "PrivateNote": f"edocAI-{document_id}", # sync status checks
            "Line": [
                {
                    "Id": "1",
                    "Amount": total_val,
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": account_id, "name": account_name} # THE ACTUAL CATEGORY
                    }
                }
            ]
        }

        # Add the date only if we successfully parsed it
        if qb_date:
            qb_payload["TxnDate"] = qb_date

        # Make Request to QuickBooks API
        url = f"{API_BASE_URL}/v3/company/{qb_conn.realm_id}/purchase?minorversion=65"
        headers = {
            "Authorization": f"Bearer {qb_conn.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        response = httpx.post(url, json=qb_payload, headers=headers)

        # Handle expired access token
        if response.status_code == 401:
            refresh_qb_token(qb_conn, session)
            headers["Authorization"] = f"Bearer {qb_conn.access_token}"
            response = httpx.post(url, json=qb_payload, headers=headers)

        if response.status_code not in [200, 201]:
            error_detail = response.json().get("Fault", {}).get("Error", [{}])[0].get("Message", "Unknown QB Error")
            raise HTTPException(status_code=500, detail=f"QuickBooks Error: {error_detail}")

        # --- Mark document as synced in DB ---
        doc.quickbooks_synced = True
        session.add(doc)
        session.commit()
        # ------------------------------------------
        return {"message": "Synced to QuickBooks successfully!"}

@router.get("/status")
def get_qb_status(current_user: models.User = Depends(get_current_user)):
    """Check if the current user has connected their QuickBooks account."""
    with next(database.get_session()) as session:
        qb_conn = session.exec(
            select(models.QuickBooksConnection).where(models.QuickBooksConnection.user_id == current_user.id)
        ).first()
        return {"connected": qb_conn is not None}

@router.get("/sync-status/{document_id}")
def check_sync_status(document_id: str, current_user: models.User = Depends(get_current_user)):
    if current_user.plan != "pro":
        return {"synced": False}
    
    with next(database.get_session()) as session:
        qb_conn = session.exec(
            select(models.QuickBooksConnection).where(models.QuickBooksConnection.user_id == current_user.id)
        ).first()
        
        if not qb_conn:
            return {"synced": False}

        # Query QuickBooks API for a Purchase that matches our document ID in the PrivateNote
        url = f"{API_BASE_URL}/v3/company/{qb_conn.realm_id}/query?query=select * from Purchase where PrivateNote = 'edocAI-{document_id}'"
        headers = {
            "Authorization": f"Bearer {qb_conn.access_token}",
            "Accept": "application/json",
        }

        response = httpx.get(url, headers=headers)

        # Handle expired token
        if response.status_code == 401:
            refresh_qb_token(qb_conn, session)
            headers["Authorization"] = f"Bearer {qb_conn.access_token}"
            response = httpx.get(url, headers=headers)

        if response.status_code == 200:
            data = response.json()
            results = data.get("QueryResponse", {}).get("Purchase", [])
            return {"synced": len(results) > 0} # If QB returns 1 or more matches, it's synced!
        
        return {"synced": False}

@router.delete("/disconnect")
def disconnect_quickbooks(current_user: models.User = Depends(get_current_user)):
    """Deletes the QuickBooks OAuth tokens for the current user."""
    with next(database.get_session()) as session:
        qb_conn = session.exec(
            select(models.QuickBooksConnection).where(models.QuickBooksConnection.user_id == current_user.id)
        ).first()
        
        if not qb_conn:
            raise HTTPException(status_code=404, detail="QuickBooks is not connected.")

        session.delete(qb_conn)
        session.commit()

    return {"message": "QuickBooks disconnected successfully."}