from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import Session, select
import database, models, auth
from datetime import date

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> models.User:
    token = credentials.credentials
    payload = auth.decode_access_token(token)
    
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    
    username = payload.get("sub")
    if username is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")
        
    with Session(database.engine) as session:
        user = session.exec(select(models.User).where(models.User.username == username)).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user

def increment_usage(user_id: str):
    """Call this AFTER a document is successfully processed to track limits."""
    current_month = date.today().replace(day=1)
    
    with next(database.get_session()) as session:
        usage = session.exec(
            select(models.UsageRecord)
            .where(models.UsageRecord.user_id == user_id)
            .where(models.UsageRecord.month == current_month)
        ).first()

        if usage:
            usage.documents_processed += 1
        else:
            usage = models.UsageRecord(user_id=user_id, month=current_month, documents_processed=1)
        
        session.add(usage)
        session.commit()