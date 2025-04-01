from datetime import datetime, timedelta
from typing import Dict, Optional
from uuid import UUID
from uuid import uuid4

from cassandra.cluster import Cluster
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer

from passlib.context import CryptContext
from pydantic import BaseModel
from cassandra.cluster import Session
from fastapi import Depends
from typing import Dict, Optional, List


from user_2 import get_current_user

# Initialize FastAPI app
app = FastAPI(title="Order Microservice")


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



class Order(BaseModel):
    restaurant_id: UUID
    products: Dict[UUID, int]  # Maps item_id to quantity
    total_price: float
    discount: Optional[float] = None
    payment_method: str
    delivery_method: str  # "delivery" or "pickup"
    address: Optional[str] = None  # Required if delivery_method is "delivery"
    estimated_delivery_time: datetime

class UpdateOrderRequest(BaseModel):
    order_id: UUID
    products: Optional[Dict[UUID, int]] = None
    delivery_method: Optional[str] = None
    address: Optional[str] = None

class CancelOrderRequest(BaseModel):
    order_id: UUID

class UpdateOrderStatusRequest(BaseModel):
    order_id: UUID
    status: str

class Discount(BaseModel):
    discount_code: str
    discount_percentage: int
    expires_at: datetime

class AddDiscountRequest(BaseModel):
    discounts: List[Discount]

# Helper function to check if the user is a worker
def verify_worker(
    current_user: UUID = Depends(get_current_user),
    session: Session = Depends(get_db_session),
):
    user_query = "SELECT worker FROM customers WHERE customer_id = %s"
    user_result = session.execute(user_query, [current_user]).one()

    if not user_result or user_result.worker != 1:
        raise HTTPException(status_code=403, detail="Worker privileges required")

    return current_user

@app.post("/orders")
async def create_order(
    order: Order,
    current_user: UUID = Depends(get_current_user),
    db=Depends(get_db_session),
):
    if order.delivery_method == "delivery" and not order.address:
        raise HTTPException(status_code=400, detail="Address is required for delivery")

    order_id = uuid4()
    delivery_person = None
    delivery_person_name = None
    delivery_person_phone = None
    restaurant_id = order.restaurant_id

    # Retrieve delivery people from the restaurant
    restaurant_row = db.execute(
        "SELECT delivery_people FROM restaurants WHERE restaurant_id = %s",
        [restaurant_id],
    ).one()

    if not restaurant_row or not restaurant_row.delivery_people:
        raise HTTPException(
            status_code=404,
            detail="No delivery people available for the specified restaurant",
        )

    # Extract delivery people UUIDs and their statuses
    delivery_people_map = restaurant_row.delivery_people  # This is a map<UUID, TEXT>
    available_delivery_person = None

    for delivery_person_id, status in delivery_people_map.items():
        if status == "Assigned":  # Check if the delivery person is available
            available_delivery_person = delivery_person_id
            break

    if not available_delivery_person:
        raise HTTPException(
            status_code=404,
            detail="No available delivery person for the specified restaurant",
        )

    # Retrieve the delivery person's details from the delivery_people table
    delivery_person_row = db.execute(
        "SELECT name, phone FROM delivery_people WHERE delivery_person_id = %s",
        [available_delivery_person],
    ).one()

    if delivery_person_row:
        delivery_person = available_delivery_person
        delivery_person_name = delivery_person_row.name
        delivery_person_phone = delivery_person_row.phone

    # Insert the order into the database
    db.execute(
        """
        INSERT INTO orders (order_id, customer_id, restaurant_id, products, total_price, discount, payment_method,
                            delivery_method, address, status, created_at, estimated_delivery_time, delivery_person,
                            delivery_person_name, delivery_person_phone)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            order_id,
            current_user,
            order.restaurant_id,
            order.products,
            order.total_price,
            order.discount,
            order.payment_method,
            order.delivery_method,
            order.address,
            "Pending",
            datetime.utcnow(),
            order.estimated_delivery_time,
            delivery_person,
            delivery_person_name,
            delivery_person_phone,
        ),
    )
    return {"message": "Order created successfully", "order_id": str(order_id)}

@app.put("/orders")
async def update_order(
    data: UpdateOrderRequest,
    current_user: UUID = Depends(get_current_user),
    db=Depends(get_db_session),
):
    order_row = db.execute(
        "SELECT * FROM orders WHERE order_id = %s AND customer_id = %s",
        [data.order_id, current_user],
    ).one()

    if not order_row:
        raise HTTPException(status_code=404, detail="Order not found or not authorized")

    if datetime.utcnow() > order_row.created_at + timedelta(minutes=30):
        raise HTTPException(
            status_code=400, detail="Cannot edit the order after 30 minutes of creation"
        )

    updates = []
    params = []

    if data.products:
        updates.append("products = %s")
        params.append(data.products)
    if data.delivery_method:
        updates.append("delivery_method = %s")
        params.append(data.delivery_method)
    if data.address:
        updates.append("address = %s")
        params.append(data.address)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(data.order_id)
    query = f"UPDATE orders SET {', '.join(updates)} WHERE order_id = %s"
    db.execute(query, params)
    return {"message": "Order updated successfully"}

@app.delete("/orders")
async def cancel_order(
    data: CancelOrderRequest,
    current_user: UUID = Depends(get_current_user),
    db=Depends(get_db_session),
):
    order_row = db.execute(
        "SELECT * FROM orders WHERE order_id = %s AND customer_id = %s",
        [data.order_id, current_user],
    ).one()

    if not order_row:
        raise HTTPException(status_code=404, detail="Order not found or not authorized")

    if datetime.utcnow() > order_row.estimated_delivery_time - timedelta(minutes=30):
        raise HTTPException(
            status_code=400, detail="Cannot cancel the order within 30 minutes of delivery"
        )

    db.execute("DELETE FROM orders WHERE order_id = %s", [data.order_id])
    return {"message": "Order canceled successfully"}

@app.put("/orders/status")
async def update_order_status(
    data: UpdateOrderStatusRequest,
    worker: UUID = Depends(verify_worker),
    db=Depends(get_db_session),
):
    db.execute(
        "UPDATE orders SET status = %s WHERE order_id = %s",
        [data.status, data.order_id],
    )
    return {"message": "Order status updated successfully"}



@app.post("/discounts")
async def add_discounts(
    data: AddDiscountRequest,
    worker: UUID = Depends(verify_worker),
    db=Depends(get_db_session),
):
    for discount in data.discounts:
        discount_id = uuid4()
        db.execute(
            """
            INSERT INTO discounts (discount_id, discount_code, discount_percentage, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                discount_id,
                discount.discount_code,
                discount.discount_percentage,
                datetime.utcnow(),
                discount.expires_at,
            ),
        )
    return {"message": "Discounts added successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8003)