from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from datetime import datetime, timezone
import httpx
import os
import database, models
from dependencies import get_current_user
from dotenv import load_dotenv
from fastapi.responses import RedirectResponse

load_dotenv()

router = APIRouter(prefix="/api/v1/quickbooks", tags=["quickbooks"])

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
def quickbooks_callback(code: str, state: str, realm_id: str = None):
    """Handles the redirect from Intuit after user authorizes."""
    
    # If Intuit didn't pass the realm_id in the initial redirect, we can't proceed.
    if not realm_id:
        # Redirect to frontend with an error message
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
        
        # Safely parse total amount (remove $ and commas)
        total_str = str(extracted.get("total_amount", "0")).replace("$", "").replace(",", "")
        try:
            total_val = float(total_str)
        except ValueError:
            total_val = 0.0

        qb_payload = {
            "AccountRef": {"value": "41", "name": "Opening Balance Equity"}, # Default account, users can map later
            "PaymentType": "Cash",
            "EntityRef": {"name": vendor_name},
            "TotalAmt": total_val,
            "Line": [
                {
                    "Id": "1",
                    "Amount": total_val,
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "41"} 
                    }
                }
            ]
        }

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

        return {"message": "Synced to QuickBooks successfully!"}

@router.get("/status")
def get_qb_status(current_user: models.User = Depends(get_current_user)):
    """Check if the current user has connected their QuickBooks account."""
    with next(database.get_session()) as session:
        qb_conn = session.exec(
            select(models.QuickBooksConnection).where(models.QuickBooksConnection.user_id == current_user.id)
        ).first()
        return {"connected": qb_conn is not None}