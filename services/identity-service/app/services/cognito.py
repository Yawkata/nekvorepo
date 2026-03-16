import boto3
import hmac
import hashlib
import base64
from fastapi import HTTPException, status
from app.core.config import settings

class CognitoService:
    def __init__(self):
        self.client = boto3.client("cognito-idp", region_name=settings.AWS_REGION)

    def _get_secret_hash(self, username: str) -> str:
        """Calculates the SecretHash required by Cognito when a Client Secret is used."""
        msg = username + settings.COGNITO_CLIENT_ID
        dig = hmac.new(
            str(settings.COGNITO_CLIENT_SECRET).encode('utf-8'),
            msg.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(dig).decode()
    
    def register_user(self, email: str, password: str, full_name: str):
        try:
            return self.client.sign_up(
                ClientId=settings.COGNITO_CLIENT_ID,
                SecretHash=self._get_secret_hash(email),
                Username=email,
                Password=password,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "name", "Value": full_name}
                ]
            )
        except self.client.exceptions.UsernameExistsException:
            raise HTTPException(status_code=400, detail="Email already registered")
        except Exception as e:
            # --- ADD THIS LOGGING ---
            import traceback
            print(f"COGNITO REGISTRATION ERROR: {str(e)}")
            traceback.print_exc() 
            # ------------------------
            raise HTTPException(status_code=500, detail=str(e))

    def login(self, email: str, password: str):
        try:
            response = self.client.admin_initiate_auth(
                UserPoolId=settings.COGNITO_USER_POOL_ID,
                ClientId=settings.COGNITO_CLIENT_ID,
                AuthFlow="ADMIN_NO_SRP_AUTH",
                AuthParameters={
                    "USERNAME": email,
                    "PASSWORD": password,
                    "SECRET_HASH": self._get_secret_hash(email)
                }
            )
            return response["AuthenticationResult"]
        except (self.client.exceptions.NotAuthorizedException, self.client.exceptions.UserNotFoundException):
            raise HTTPException(status_code=401, detail="Invalid email or password")