from datetime import datetime, timedelta
from typing import Dict, Optional
from uuid import UUID
from uuid import uuid4

from cassandra.cluster import Cluster
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
import jwt

from passlib.context import CryptContext
from pydantic import BaseModel
from cassandra.cluster import Session
from fastapi import Depends
from typing import Dict, Optional, List

import httpx

from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut

# Initialize FastAPI app
app = FastAPI(title="Order Microservice")


# Security configurations
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
SECRET_KEY = "your-secret-key"  # Move to environment variable
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

AUTH_SERVICE_URL = "http://user_service"  # Replace with the actual URL of your auth service

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        customer_id: str = payload.get("sub")
        if customer_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        # Return the customer_id as a string
        customer_id = UUID(customer_id)  # Convert to UUID if needed
        return customer_id
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Database connection
# def get_db_session():
#     cluster = Cluster(['host.docker.internal'], protocol_version=4)
#     session = cluster.connect('pantastic')
#     return session

def get_db_session():
    import os
    # Get the Cassandra host from the environment variable
    cassandra_host = os.getenv("CASSANDRA_HOST", "127.0.0.1")  # Default to localhost if not set
    cluster = Cluster([cassandra_host], protocol_version=4)
    session = cluster.connect('pantastic')  # Replace 'pantastic' with your keyspace name
    return session

class Order(BaseModel):
    restaurant_id: UUID
    products: Dict[UUID, int]  # Maps item_id to quantity
    discount: Optional[str] = None  # Discount code
    payment_method: str
    delivery_method: str  # "delivery" or "pickup"
    address: Optional[str] = None  # Required if delivery_method is "delivery"

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
    current_user: str = Depends(get_current_user),  # Now returns a string
    session: Session = Depends(get_db_session),
):
    user_query = "SELECT worker FROM customers WHERE customer_id = %s"
    user_result = session.execute(user_query, [current_user]).one()

    if not user_result or user_result.worker != 1:
        raise HTTPException(status_code=403, detail="Worker privileges required")

    return current_user

def get_lat_long(address):
    geolocator = Nominatim(user_agent="myGeocoder", timeout=10)  # Set a timeout
    try:
        location = geolocator.geocode(address)
        if location:
            return location.latitude, location.longitude
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Could not find coordinates for the provided address: {address}",
            )
    except GeocoderTimedOut:
        raise HTTPException(
            status_code=504,
            detail="Geocoding service timed out. Please try again later.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while processing the address: {str(e)}",
        )

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

    restaurant_row = db.execute(
        "SELECT latitude, longitude FROM restaurants WHERE restaurant_id = %s",
        [restaurant_id],
    ).one()

    if not restaurant_row:
        raise HTTPException(
            status_code=404,
            detail="Restaurant not found",
        )

    restaurant_coordinates = (restaurant_row.latitude, restaurant_row.longitude)

    # Fetch delivery address coordinates
    delivery_coordinates = get_lat_long(order.address)
    if not delivery_coordinates:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve coordinates for the delivery address",
        )

    # Calculate the distance between the restaurant and the delivery address
    distance_km = geodesic(restaurant_coordinates, delivery_coordinates).km

    # Check if the distance exceeds 20 km
    if distance_km > 20:
        raise HTTPException(
            status_code=400,
            detail=f"Delivery distance of {distance_km:.2f} km exceeds the maximum allowed distance of 20 km",
        )

    # Calculate the delivery fee
    delivery_coefficient = 2.5  # Example coefficient (you can adjust this value)
    delivery_fee = delivery_coefficient * distance_km


  # Fetch all item prices in a single query
    placeholders = ','.join(['%s'] * len(order.products.keys()))
    query = f"SELECT item_id, price FROM items WHERE restaurant_id = %s AND item_id IN ({placeholders}) ALLOW FILTERING"
    params = [restaurant_id] + list(order.products.keys())  # Convert dict_keys to a list

    rows = db.execute(query, params)


    # Build a dictionary of item prices
    item_prices = {row.item_id: row.price for row in rows}

    # Calculate the total price
    total_price = 0
    for item_id, quantity in order.products.items():
        if item_id not in item_prices:
            raise HTTPException(
                status_code=404,
                detail=f"Item with ID {item_id} not found in the specified restaurant",
            )
        total_price += item_prices[item_id] * quantity

    # Apply discount if provided
    if order.discount:
        discount_row = db.execute(
            "SELECT discount_percentage, expires_at FROM discounts WHERE discount_code = %s",
            [order.discount],
        ).one()
        if not discount_row:
            raise HTTPException(status_code=404, detail="Invalid discount code")
        if discount_row.expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Discount code has expired")
        discount_percentage = discount_row.discount_percentage
        total_price -= total_price * (discount_percentage / 100)

    total_price = float(total_price)
    total_price += delivery_fee  # Add delivery fee to the total price

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

    estimated_delivery_time = datetime.utcnow() + timedelta(minutes=90)

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
            total_price,
            order.discount,
            order.payment_method,
            order.delivery_method,
            order.address,
            "Pending",
            datetime.utcnow(),
            estimated_delivery_time,
            delivery_person,
            delivery_person_name,
            delivery_person_phone,
        ),
    )
    return {"message": "Order created successfully", "order_id": str(order_id)}

#TODO need to make checks for everything in this function
@app.put("/orders")
async def update_order(
    data: UpdateOrderRequest,
    current_user: UUID = Depends(get_current_user),
    db=Depends(get_db_session),
):
    order_row = db.execute(
        "SELECT * FROM orders WHERE order_id = %s AND customer_id = %s ALLOW FILTERING",
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
        "SELECT * FROM orders WHERE order_id = %s AND customer_id = %s ALLOW FILTERING",
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


#TODO need to make checks if the worker is working in the resuaurant
@app.put("/orders/status")
async def update_order_status(
    data: UpdateOrderStatusRequest,
    worker: UUID = Depends(verify_worker),
    db=Depends(get_db_session),
):
    if data.status not in ["Pending", "In Progress", "Delivered", "Canceled"]:
        raise HTTPException(
            status_code=400, detail="Invalid status. Allowed values are: Pending, In Progress, Delivered, Canceled"
        )
    
    if data.status == "Delivered":
        order_row = db.execute(
            "SELECT * FROM orders WHERE order_id = %s ALLOW FILTERING",
            [data.order_id],
        ).one()

        if not order_row:
            raise HTTPException(status_code=404, detail="Order not found")

        if order_row.status == "Delivered":
            raise HTTPException(status_code=400, detail="Order is already marked as Delivered")

        delivered_at = datetime.utcnow()
        db.execute(
            "UPDATE orders SET delivery_time = %s WHERE order_id = %s",
            [delivered_at, data.order_id],
        )


    db.execute(
        "UPDATE orders SET status = %s WHERE order_id = %s",
        [data.status, data.order_id],
    )
    return {"message": "Order status updated successfully"}



# @app.post("/discounts")
# async def add_discounts(
#     data: AddDiscountRequest,
#     worker: UUID = Depends(verify_worker),
#     db=Depends(get_db_session),
# ):
#     for discount in data.discounts:
#         discount_id = uuid4()
#         db.execute(
#             """
#             INSERT INTO discounts (discount_id, discount_code, discount_percentage, created_at, expires_at)
#             VALUES (%s, %s, %s, %s, %s)
#             """,
#             (
#                 discount_id,
#                 discount.discount_code,
#                 discount.discount_percentage,
#                 datetime.utcnow(),
#                 discount.expires_at,
#             ),
#         )
#     return {"message": "Discounts added successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)