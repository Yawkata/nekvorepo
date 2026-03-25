import boto3
import hmac
import hashlib
import base64
import structlog
from botocore.exceptions import ClientError
from fastapi import HTTPException, status
from app.core.config import settings

log = structlog.get_logger()

# Module-level singleton — boto3 client creation is expensive (credential
# resolution, connection pool init). Re-using one instance across requests
# is the standard pattern and is thread-safe for read operations.
_cognito_client = boto3.client("cognito-idp", region_name=settings.AWS_REGION)


class CognitoService:
    def __init__(self):
        self.client = _cognito_client

    def _get_secret_hash(self, username: str) -> str:
        """Calculates the SecretHash required by Cognito when a Client Secret is used."""
        msg = username + settings.COGNITO_CLIENT_ID
        dig = hmac.new(
            str(settings.COGNITO_CLIENT_SECRET).encode('utf-8'),
            msg.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(dig).decode()
    
    def register_user(self, email: str, password: str, full_name: str) -> dict:
        try:
            self.client.sign_up(
                ClientId=settings.COGNITO_CLIENT_ID,
                SecretHash=self._get_secret_hash(email),
                Username=email,
                Password=password,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "name", "Value": full_name},
                ],
            )
            return {"message": "Registration successful. Please check your email for the verification code."}
        except self.client.exceptions.UsernameExistsException:
            raise HTTPException(status_code=400, detail="Email already registered.")
        except self.client.exceptions.InvalidPasswordException:
            raise HTTPException(
                status_code=400,
                detail="Password does not meet requirements (min 8 chars, uppercase, number, symbol).",
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            log.error("cognito_register_failed", error_code=code)
            raise HTTPException(status_code=500, detail="Registration failed. Please try again.")
        except Exception:
            log.exception("cognito_register_unexpected_error")
            raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    def login(self, email: str, password: str) -> dict:
        try:
            response = self.client.admin_initiate_auth(
                UserPoolId=settings.COGNITO_USER_POOL_ID,
                ClientId=settings.COGNITO_CLIENT_ID,
                AuthFlow="ADMIN_NO_SRP_AUTH",
                AuthParameters={
                    "USERNAME": email,
                    "PASSWORD": password,
                    "SECRET_HASH": self._get_secret_hash(email),
                },
            )
            return response["AuthenticationResult"]
        except self.client.exceptions.UserNotConfirmedException:
            raise HTTPException(
                status_code=403,
                detail="Account not confirmed. Check your email for the verification code.",
            )
        except (
            self.client.exceptions.NotAuthorizedException,
            self.client.exceptions.UserNotFoundException,
        ):
            raise HTTPException(status_code=401, detail="Invalid email or password")

    def confirm_user(self, email: str, code: str) -> None:
        try:
            self.client.confirm_sign_up(
                ClientId=settings.COGNITO_CLIENT_ID,
                SecretHash=self._get_secret_hash(email),
                Username=email,
                ConfirmationCode=code,
            )
        except self.client.exceptions.CodeMismatchException:
            raise HTTPException(status_code=400, detail="Invalid confirmation code.")
        except self.client.exceptions.ExpiredCodeException:
            raise HTTPException(status_code=400, detail="Confirmation code has expired. Request a new one.")
        except self.client.exceptions.NotAuthorizedException:
            raise HTTPException(status_code=400, detail="Account is already confirmed.")
        except self.client.exceptions.UserNotFoundException:
            raise HTTPException(status_code=404, detail="User not found.")
        except self.client.exceptions.LimitExceededException:
            raise HTTPException(status_code=429, detail="Too many attempts. Please wait before trying again.")

    def refresh_session(self, refresh_token: str, email: str) -> dict:
        """
        Exchange a Cognito refresh token for a new AuthenticationResult.

        With username_attributes=["email"], Cognito auto-generates a UUID as
        the actual username and treats the email as a sign-in alias only.
        Login works with HMAC(secret, email + client_id) because we explicitly
        send USERNAME=email and Cognito verifies against the value we provide.
        For REFRESH_TOKEN_AUTH however, Cognito resolves the user from the
        opaque refresh token and verifies the SECRET_HASH against the internal
        UUID username — so we must look it up first via admin_get_user.
        """
        try:
            # Resolve the Cognito-internal UUID username from the email alias
            user_info = self.client.admin_get_user(
                UserPoolId=settings.COGNITO_USER_POOL_ID,
                Username=email,
            )
            cognito_username = user_info["Username"]  # the UUID

            response = self.client.initiate_auth(
                ClientId=settings.COGNITO_CLIENT_ID,
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters={
                    "REFRESH_TOKEN": refresh_token,
                    "SECRET_HASH": self._get_secret_hash(cognito_username),
                },
            )
            return response["AuthenticationResult"]
        except ClientError as e:
            code = e.response["Error"]["Code"]
            message = e.response["Error"]["Message"]
            log.error("cognito_refresh_failed", error_code=code, error_message=message)
            if code == "NotAuthorizedException":
                raise HTTPException(status_code=401, detail="Refresh token is invalid or expired")
            if code == "UserNotFoundException":
                raise HTTPException(status_code=401, detail="User not found")
            raise HTTPException(status_code=500, detail="Token refresh failed. Please log in again.")