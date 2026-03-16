from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "identity-service"
    API_V1_STR: str = "/v1"
    
    # AWS Configuration
    AWS_REGION: str = "us-east-1"
    COGNITO_USER_POOL_ID: str
    COGNITO_CLIENT_ID: str
    COGNITO_CLIENT_SECRET: str
    
    # Database
    DATABASE_URL: str
    
    # Passport Secret (for signing our internal JWT)
    # In 2026, we use a shared secret or RSA key between microservices
    PASSPORT_SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    model_config = SettingsConfigDict(case_sensitive=True)

settings = Settings()