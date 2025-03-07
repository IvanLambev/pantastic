import unittest
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
import jwt
from uuid import UUID, uuid4
from passlib.context import CryptContext
from fastapi import HTTPException

# Import your FastAPI app and dependencies
from user_2 import (
    app, UserCreate, UserLogin, UserDelete,
    create_access_token, get_current_user,
    SECRET_KEY, ALGORITHM
)


class TestCreateAccessToken(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.test_user = {
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
            "phone": "1234567890",
            "city": "Test City",
            "password": "testpassword123"
        }
        self.test_user_id = uuid4()

    def test_create_access_token_valid(self):
        data = {"sub": str(self.test_user_id)}
        token = create_access_token(data)

        # Verify token can be decoded
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        self.assertEqual(payload["sub"], str(self.test_user_id))
        self.assertIn("exp", payload)

    def test_create_access_token_expiration(self):
        data = {"sub": str(self.test_user_id)}
        token = create_access_token(data)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        exp_time = datetime.fromtimestamp(payload["exp"])
        now = datetime.utcnow()
        time_difference = exp_time - now

        # Check if expiration time is between 29 and 31 minutes
        self.assertGreater(time_difference, timedelta(minutes=29))
        self.assertLess(time_difference, timedelta(minutes=31))


class TestGetCurrentUser(unittest.TestCase):
    async def asyncSetUp(self):
        self.client = TestClient(app)
        self.test_user_id = uuid4()

    @pytest.mark.asyncio
    async def test_valid_token(self):
        token = create_access_token({"sub": str(self.test_user_id)})
        user_id = await get_current_user(token)
        self.assertEqual(user_id, self.test_user_id)

    @pytest.mark.asyncio
    async def test_invalid_token(self):
        with self.assertRaises(HTTPException) as context:
            await get_current_user("invalid_token")
        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "Invalid token")


class TestRegisterEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.test_user = {
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
            "phone": "1234567890",
            "city": "Test City",
            "password": "testpassword123"
        }

    @patch('user_2.get_db_session')
    @patch('user_2.pwd_context')
    def test_register_new_user(self, mock_pwd_context, mock_db_session):
        mock_db_session.return_value.execute.return_value.one.return_value = None
        mock_pwd_context.hash.return_value = "hashed_password"

        response = self.client.post("/register", json=self.test_user)

        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.json())
        self.assertIn("token_type", response.json())
        self.assertEqual(response.json()["token_type"], "bearer")


# Add a main block to run the tests
if __name__ == '__main__':
    unittest.main()