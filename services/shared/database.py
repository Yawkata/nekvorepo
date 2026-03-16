import os
from sqlmodel import create_engine, Session

DATABASE_URL = os.getenv("DATABASE_URL") # Provided by Docker-Compose or EKS Secrets

# 2026 Best Practice: Use connection pooling and SSL for RDS
connect_args = {}
if "amazonaws.com" in (DATABASE_URL or ""):
    connect_args = {"sslmode": "verify-full"}

engine = create_engine(
    DATABASE_URL, 
    echo=os.getenv("DEBUG", "false").lower() == "true",
    pool_size=20,
    max_overflow=0,
    connect_args=connect_args
)

def get_session():
    with Session(engine) as session:
        yield session