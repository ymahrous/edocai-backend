# auth_routes.py
from fastapi import APIRouter, HTTPException, Depends, status
from sqlmodel import Session, select
from pydantic import BaseModel
import database, models, auth

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

class LoginRequest(BaseModel):
    username: str
    password: str

class SignupRequest(BaseModel):
    username: str
    password: str

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
    
    token = auth.create_access_token(data={"sub": user.username})
    return TokenResponse(access_token=token)