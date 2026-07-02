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
from document_routes import router as document_router
from quickbooks_routes import router as quickbooks_router
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
app.include_router(document_router)
app.include_router(quickbooks_router)