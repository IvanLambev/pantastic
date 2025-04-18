from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from cassandra.cluster import Cluster
import jwt
from passlib.context import CryptContext

# Initialize FastAPI app
app = FastAPI(title="User Microservice")

# Security configurations
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
SECRET_KEY = "your-secret-key"  # Move to environment variable
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


# Database connection
def get_db_session():
    cluster = Cluster(['host.docker.internal'], protocol_version=4)
    session = cluster.connect('pantastic')
    return session


# Models (keep the same)
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
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")



@app.post("/register")
async def register(user: UserCreate):
    session = get_db_session()

    # Check if email exists (now using plain email)
    result = session.execute("SELECT email FROM customers WHERE email = %s",
                             [user.email])

    if result.one():
        raise HTTPException(status_code=400, detail="Email already registered")

    customer_id = uuid4()
    hashed_password = pwd_context.hash(user.password)

    # Insert user with plain text data (except password)
    session.execute("""
        INSERT INTO customers (
            customer_id, admin, email, password, first_name, last_name,
            phone, city, total_orders, total_spent, worker, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        customer_id,
        0,
        user.email,
        hashed_password,
        user.first_name,
        user.last_name,
        user.phone,
        user.city,
        0,
        0.0,
        0,
        datetime.utcnow()
    ))

    access_token = create_access_token({"sub": str(customer_id)})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/login")
async def login(user_credentials: UserLogin):
    session = get_db_session()

    # Find user by plain email
    result = session.execute(
        "SELECT customer_id, password FROM customers WHERE email = %s",
        [user_credentials.email]
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
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "city": user.city,
        "total_orders": user.total_orders,
        "total_spent": float(user.total_spent),
        "created_at": user.created_at,
        "admin": user.admin,
        "worker": user.worker,
    }

#
# @app.post("/address")
# async def add_address(address: Address, customer_id: UUID = Depends(get_current_user)):
#     session = get_db_session()
#
#     if address.is_default:
#         session.execute(
#             "UPDATE user_addresses SET is_default = false WHERE customer_id = %s",
#             [customer_id]
#         )
#
#     session.execute("""
#         INSERT INTO user_addresses (customer_id, address, is_default, created_at)
#         VALUES (%s, %s, %s, %s)
#     """, (customer_id, address.address, address.is_default, datetime.utcnow()))
#
#     return {"message": "Address added successfully"}
#
#
# @app.get("/addresses")
# async def get_addresses(customer_id: UUID = Depends(get_current_user)):
#     session = get_db_session()
#     addresses = session.execute(
#         "SELECT address, is_default FROM user_addresses WHERE customer_id = %s",
#         [customer_id]
#     )
#     return [{
#         "address": addr.address,
#         "is_default": addr.is_default
#     } for addr in addresses]
#

@app.delete("/user/delete")
async def delete_user_by_email(user_data: UserDelete):
    try:
        session = get_db_session()

        # Find user using plain email
        user = session.execute(
            "SELECT customer_id, email FROM customers WHERE email = %s ALLOW FILTERING",
            [user_data.email]
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

        return {
            "message": "User and associated data deleted successfully",
            "email": user_data.email
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete user: {str(e)}"

                 )




if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)