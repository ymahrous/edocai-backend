from fastapi import APIRouter, HTTPException, Depends, status
from sqlmodel import Session, select
from pydantic import BaseModel
import database, models, auth
from dependencies import get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

class LoginRequest(BaseModel):
    username: str
    password: str

class SignupRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest, session: Session = Depends(database.get_session)):
    # 1. Check if user already exists
    existing_user = session.exec(select(models.User).where(models.User.username == request.username)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # 2. Create new user
    new_user = models.User(
        username=request.username,
        hashed_password=auth.get_password_hash(request.password)
    )
    session.add(new_user)
    session.commit()
    session.refresh(new_user)
    
    # 3. Log them in automatically by returning a token
    token = auth.create_access_token(data={"sub": new_user.username})
    return TokenResponse(access_token=token)

@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, session: Session = Depends(database.get_session)):
    user = session.exec(select(models.User).where(models.User.username == request.username)).first()
    if not user or not auth.verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    
    token = auth.create_access_token(data={"sub": user.username, "plan": user.plan})
    return TokenResponse(access_token=token)

@router.post("/change-password")
def change_password(
    request: ChangePasswordRequest,
    user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session),
):
    if not auth.verify_password(request.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect.")
    user.hashed_password = auth.get_password_hash(request.new_password)
    session.add(user)
    session.commit()
    return {"message": "Password updated successfully."}


@router.delete("/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    user: models.User = Depends(get_current_user),
    session: Session = Depends(database.get_session),
):
    documents = session.exec(select(models.Document).where(models.Document.owner_id == user.id)).all()
    usage_records = session.exec(select(models.UsageRecord).where(models.UsageRecord.user_id == user.id)).all()
    subscriptions = session.exec(select(models.Subscription).where(models.Subscription.user_id == user.id)).all()

    for doc in documents:
        session.delete(doc)

    for record in usage_records:
        session.delete(record)
        
    for sub in subscriptions:
        session.delete(sub)

    session.delete(user)
    session.commit()
    return {"message": "Account deleted successfully."}