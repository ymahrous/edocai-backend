from sqlmodel import SQLModel, create_engine, Session, select
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

def init_db():
    # Just create tables. No more fake admin users.
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session