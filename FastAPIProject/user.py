from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from cassandra.cluster import Cluster
import jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet
import os
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64


# Initialize FastAPI app
app = FastAPI(title="User Microservice")

# Security configurations
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
SECRET_KEY = "your-secret-key"  # Move to environment variable
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def get_or_create_key():
    """Get existing key or create and store a new one"""
    key_file = Path("encryption.key")

    if key_file.exists():
        with open(key_file, "rb") as f:
            return f.read()

    # Generate new key if it doesn't exist
    key = Fernet.generate_key()
    print("Generating key...")

    # Save the key
    with open(key_file, "wb") as f:
        f.write(key)

    return key


# Initialize encryption key for sensitive data
ENCRYPTION_KEY = get_or_create_key()
fernet = Fernet(ENCRYPTION_KEY)



# Database connection
def get_db_session():
    cluster = Cluster(['host.docker.internal'], protocol_version=4)  # Removed connection_class
    session = cluster.connect('pantastic')
    return session




# Models
class UserBase(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    phone: str
    city: str


class UserCreate(UserBase):
    password: str

class UserDelete(BaseModel):
    email: EmailStr


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Address(BaseModel):
    address: str
    is_default: bool = False


# Helper functions
def get_deterministic_key(data: str) -> bytes:
    """Generate a deterministic key for a given string"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=ENCRYPTION_KEY[:16],  # Use first 16 bytes of main key as salt
        iterations=100000,
        backend=default_backend()
    )
    return base64.urlsafe_b64encode(kdf.derive(data.encode()))


def encrypt_searchable_data(data: str) -> str:
    """Encrypt data in a deterministic way for searchable fields"""
    key = get_deterministic_key(data)
    f = Fernet(key)
    return f.encrypt(data.encode()).decode()


def encrypt_data(data: str) -> str:
    """Encrypt data in a non-deterministic way for non-searchable fields"""
    return fernet.encrypt(data.encode()).decode()


def decrypt_data(data: str) -> str:
    """Decrypt data"""
    return fernet.decrypt(data.encode()).decode()



def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        customer_id: str = payload.get("sub")
        if customer_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return UUID(customer_id)
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# API Endpoints
@app.post("/register")
async def register(user: UserCreate):
    session = get_db_session()

    # Check if email exists
    result = session.execute("SELECT email FROM customers WHERE email = %s",
                             [encrypt_searchable_data(user.email)])

    if result.one():
        raise HTTPException(status_code=400, detail="Email already registered")

    customer_id = uuid4()
    hashed_password = pwd_context.hash(user.password)

    # Encrypt sensitive data
    encrypted_data = {
        "email": encrypt_searchable_data(user.email),
        "first_name": encrypt_data(user.first_name),
        "last_name": encrypt_data(user.last_name),
        "phone": encrypt_data(user.phone),
        "city": encrypt_data(user.city)
    }

    # Insert user
    session.execute("""
        INSERT INTO customers (
            customer_id, email, password, first_name, last_name,
            phone, city, total_orders, total_spent, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        customer_id,
        encrypted_data["email"],
        hashed_password,
        encrypted_data["first_name"],
        encrypted_data["last_name"],
        encrypted_data["phone"],
        encrypted_data["city"],
        0,
        0.0,
        datetime.utcnow()
    ))

    access_token = create_access_token({"sub": str(customer_id)})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/login")
async def login(user_credentials: UserLogin):
    session = get_db_session()

    # Find user by email
    result = session.execute(
        "SELECT customer_id, password FROM customers WHERE email = %s",
        [encrypt_searchable_data(user_credentials.email)]
    ).one()

    if not result or not pwd_context.verify(user_credentials.password, result.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token({"sub": str(result.customer_id)})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/user-info")
async def get_user_info(customer_id: UUID = Depends(get_current_user)):
    session = get_db_session()
    user = session.execute("SELECT * FROM customers WHERE customer_id = %s", [customer_id]).one()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "customer_id": user.customer_id,
        "email": decrypt_data(user.email),
        "first_name": decrypt_data(user.first_name),
        "last_name": decrypt_data(user.last_name),
        "phone": decrypt_data(user.phone),
        "city": decrypt_data(user.city),
        "total_orders": user.total_orders,
        "total_spent": float(user.total_spent),
        "created_at": user.created_at
    }


@app.post("/address")
async def add_address(address: Address, customer_id: UUID = Depends(get_current_user)):
    session = get_db_session()

    # If this is the default address, remove default flag from other addresses
    if address.is_default:
        session.execute(
            "UPDATE user_addresses SET is_default = false WHERE customer_id = %s",
            [customer_id]
        )

    session.execute("""
        INSERT INTO user_addresses (customer_id, address, is_default, created_at)
        VALUES (%s, %s, %s, %s)
    """, (customer_id, encrypt_data(address.address), address.is_default, datetime.utcnow()))

    return {"message": "Address added successfully"}


@app.get("/addresses")
async def get_addresses(customer_id: UUID = Depends(get_current_user)):
    session = get_db_session()
    addresses = session.execute(
        "SELECT address, is_default FROM user_addresses WHERE customer_id = %s",
        [customer_id]
    )
    return [{
        "address": decrypt_data(addr.address),
        "is_default": addr.is_default
    } for addr in addresses]


@app.delete("/user/delete")
async def delete_user_by_email(user_data: UserDelete):
    """
    Delete user and all associated data by email
    """
    try:
        session = get_db_session()

        # Encrypt the email using encrypt_searchable_data (consistency in the query and storage)
        encrypted_email = encrypt_searchable_data(user_data.email)

        print(f"Encrypted email for query: {encrypted_email}")  # Log encrypted email for debugging

        # Find user in customers table using encrypted email
        user = session.execute(
            "SELECT customer_id, email FROM customers WHERE email = %s ALLOW FILTERING",
            [encrypted_email]
        ).one()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        customer_id = user.customer_id

        # Delete associated orders
        session.execute(
            "DELETE FROM orders WHERE customer_id = %s",
            [customer_id]
        )

        # Delete the user
        session.execute(
            "DELETE FROM customers WHERE customer_id = %s",
            [customer_id]
        )

        # Decrypt email for response
        decrypted_email = decrypt_data(user.email)

        return {
            "message": "User and associated data deleted successfully",
            "email": decrypted_email
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete user: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)