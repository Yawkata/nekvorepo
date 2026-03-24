import os
from sqlmodel import create_engine, Session

DATABASE_URL = os.getenv("DATABASE_URL")  # Provided by Docker-Compose or EKS Secrets

# Statement timeout (5s) and SSL for RDS
connect_args: dict = {
    "options": "-c statement_timeout=5000"  # 5 000 ms = 5 s
}
if "amazonaws.com" in (DATABASE_URL or ""):
    connect_args["sslmode"] = "verify-full"

engine = create_engine(
    DATABASE_URL,
    echo=os.getenv("DEBUG", "false").lower() == "true",
    pool_size=20,
    max_overflow=0,
    pool_timeout=3,      # fail fast if no connection available within 3 s
    connect_args=connect_args,
)

def get_session():
    with Session(engine) as session:
        yield session