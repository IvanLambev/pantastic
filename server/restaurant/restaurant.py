from datetime import datetime, timedelta
from typing import Dict, Optional
from uuid import UUID
from uuid import uuid4

from cassandra.cluster import Cluster
from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File
from fastapi.security import OAuth2PasswordBearer

from passlib.context import CryptContext
from pydantic import BaseModel
from cassandra.cluster import Session
from fastapi import Depends
from typing import Dict, List
import httpx
import boto3
import io

from geopy.geocoders import Nominatim

# Initialize FastAPI app
app = FastAPI(title="restaurant Microservice")


# Security configurations
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
SECRET_KEY = "your-secret-key"  # Move to environment variable
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

AUTH_SERVICE_URL = "http://user_service:8000"  # Replace with the actual URL of your auth service

async def get_current_user(token: str = Depends(oauth2_scheme)):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{AUTH_SERVICE_URL}/validate-token", params={"token": token})
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.json().get("detail"))
        return response.json()["customer_id"]

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


# Models
class Restaurant(BaseModel):
    name: str
    address: str
    opening_hours: Dict[str, str]  # Example: {"Monday": "9:00-18:00", "Tuesday": "9:00-18:00"}

class UpdateRestaurantRequest(BaseModel):
    restaurant_id: UUID
    restaurant: Restaurant

class DeleteRestaurantRequest(BaseModel):
    restaurant_id: UUID

class DeliveryPerson(BaseModel):
    name: str
    phone: str

class DeleteDeliveryPersonRequest(BaseModel):
    delivery_person_id: UUID

class UpdateDeliveryPersonRequest(BaseModel):
    delivery_person_id: UUID
    person: DeliveryPerson

class AssignDeliveryPersonRequest(BaseModel):
    restaurant_id: UUID
    delivery_person_id: UUID

class User(BaseModel):
    user_id: UUID
    admin: bool

class Item(BaseModel):
    item_id: Optional[UUID] = None  # Optional for creation, auto-generated if not provided
    name: str
    description: Optional[str] = None
    price: float

class AddItemsRequest(BaseModel):
    restaurant_id: UUID
    items: List[Item]  # List of items to add

class UpdateItemRequest(BaseModel):
    item_id: UUID
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None

class DeleteItemRequest(BaseModel):
    item_id: UUID

class GetItemsRequest(BaseModel):
    restaurant_id: UUID

def verify_admin(
    current_user: str = Depends(get_current_user),  # current_user is a string (from token)
    session: Session = Depends(get_db_session),
):
    try:
        # Convert the string customer_id to a UUID object
        current_user_uuid = UUID(current_user)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid customer_id format")

    user_query = "SELECT admin FROM customers WHERE customer_id = %s"
    user_result = session.execute(user_query, [current_user_uuid]).one()

    if not user_result or user_result.admin != 1:
        raise HTTPException(status_code=403, detail="Not authorized")

    return current_user_uuid # Return the user ID if authorized


def get_coordinates(address):
    geolocator = Nominatim(user_agent="myGeocoder")  # User agent is required
    location = geolocator.geocode(address)
    if location:
        return {'latitude': location.latitude, 'longitude': location.longitude}
        #return location.latitude, location.longitude
    return None

# Initialize S3 client
s3 = boto3.client("s3")
BUCKET_NAME = "pantastic-images"

@app.post("/restaurants")
async def add_restaurant(restaurant: Restaurant, user: User = Depends(verify_admin), db=Depends(get_db_session)):
    restaurant_id = uuid4()
    coordinates = get_coordinates(restaurant.address)
    db.execute("""
        INSERT INTO restaurants (restaurant_id, name, address, opening_hours, latitude, longitude, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (restaurant_id, restaurant.name, restaurant.address, restaurant.opening_hours, coordinates['latitude'], coordinates['longitude'], datetime.utcnow()))
    return {"message": "Restaurant added successfully", "restaurant_id": str(restaurant_id)}

@app.get("/restaurants")
async def get_restaurants(db=Depends(get_db_session)):
    rows = db.execute("SELECT * FROM restaurants").all()
    return rows

@app.put("/restaurants")
async def update_restaurant(
    data: UpdateRestaurantRequest,  # Use the new Pydantic model
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
):
    restaurant_id = data.restaurant_id
    restaurant = data.restaurant

    coordinates = get_coordinates(restaurant.address)
    db.execute(
        """
        UPDATE restaurants SET name = %s, address = %s, opening_hours = %s, latitude = %s, longitude = %s
        WHERE restaurant_id = %s
        """,
        (
            restaurant.name,
            restaurant.address,
            restaurant.opening_hours,
            coordinates["latitude"],
            coordinates["longitude"],
            restaurant_id,
        ),
    )
    return {"message": "Restaurant updated successfully"}

@app.delete("/restaurants")
async def delete_restaurant(
    data: DeleteRestaurantRequest,  # Use a Pydantic model to parse the request body
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
):
    restaurant_id = data.restaurant_id
    db.execute("DELETE FROM restaurants WHERE restaurant_id = %s", [restaurant_id])
    return {"message": "Restaurant deleted successfully"}

# Add/remove delivery people
@app.post("/delivery-people")
async def add_delivery_person(person: DeliveryPerson, user: User = Depends(verify_admin), db=Depends(get_db_session)):
    delivery_person_id = uuid4()
    db.execute("""
        INSERT INTO delivery_people (delivery_person_id, name, phone, created_at)
        VALUES (%s, %s, %s, %s)
    """, (delivery_person_id, person.name, person.phone, datetime.utcnow()))
    return {"message": "Delivery person added successfully", "delivery_person_id": str(delivery_person_id)}

@app.delete("/delivery-people")
async def remove_delivery_person(
    data: DeleteDeliveryPersonRequest,  # Use a Pydantic model to parse the request body
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
):
    delivery_person_id = data.delivery_person_id
    db.execute("DELETE FROM delivery_people WHERE delivery_person_id = %s", [delivery_person_id])
    return {"message": "Delivery person removed successfully"}

@app.get("/delivery-people")
async def get_delivery_people(user: User = Depends(verify_admin), db=Depends(get_db_session)):
    rows = db.execute("SELECT * FROM delivery_people").all()
    return rows

@app.put("/delivery-people")
async def update_delivery_person(
    data: UpdateDeliveryPersonRequest,  # Use a Pydantic model to parse the request body
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
):
    delivery_person_id = data.delivery_person_id
    person = data.person

    db.execute(
        """
        UPDATE delivery_people SET name = %s, phone = %s
        WHERE delivery_person_id = %s
        """,
        (person.name, person.phone, delivery_person_id),
    )
    return {"message": "Delivery person updated successfully"}


# Assaign delivery person to restaurant
@app.post("/assign-delivery-person-to-restaurant")
async def assign_delivery_person(
    data: AssignDeliveryPersonRequest,  # Use a Pydantic model to parse the request body
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
):
    restaurant_id = data.restaurant_id
    delivery_person_id = data.delivery_person_id

    db.execute(
        """
        UPDATE restaurants
        SET delivery_people = delivery_people + {%s: %s}
        WHERE restaurant_id = %s
        """,
        (delivery_person_id, "Assigned", restaurant_id),
    )
    return {"message": "Delivery person assigned to restaurant successfully"}

@app.delete("/unassign-delivery-person-from-restaurant")
async def unassign_delivery_person(
    data: AssignDeliveryPersonRequest,  # Use a Pydantic model to parse the request body
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
):
    restaurant_id = data.restaurant_id
    delivery_person_id = data.delivery_person_id

    db.execute(
        """
        UPDATE restaurants
        SET delivery_people = delivery_people - {%s}
        WHERE restaurant_id = %s
        """,
        (delivery_person_id, restaurant_id),
    )
    return {"message": "Delivery person unassigned from restaurant successfully"}

@app.post("/items")
async def add_items(
    data: AddItemsRequest,  # Use a Pydantic model to parse the request body
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
    file: UploadFile = File(...),
):
    restaurant_id = data.restaurant_id
    items = data.items

    for item in items:
        item_id = item.item_id or uuid4()  # Generate a new UUID if not provided

        # Upload image to S3
        contents = await file.read()
        file_like_object = io.BytesIO(contents)
        s3.upload_fileobj(
            Fileobj=file_like_object,
            Bucket=BUCKET_NAME,
            Key=f"menu-items/{item_id}.jpg",
        )

        db.execute(
            """
            INSERT INTO items (item_id, restaurant_id, name, description, price, created_at, image_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item_id,
                restaurant_id,
                item.name,
                item.description,
                item.price,
                datetime.utcnow(),
                f"https://{BUCKET_NAME}.s3.amazonaws.com/menu-items/{item_id}.jpg",
            ),
        )
    return {"message": "Items added successfully"}

@app.get("/{restaurant_id}/items")
async def get_items(
    restaurant_id: UUID,  # This will be parsed from the URL path
    db=Depends(get_db_session),
):
    rows = db.execute(
        "SELECT * FROM items WHERE restaurant_id = %s ALLOW FILTERING", [restaurant_id]
    ).all()
    return rows

@app.put("/items")
async def update_item(
    data: UpdateItemRequest,  # Use a Pydantic model to parse the request body
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
    file: Optional[UploadFile] = None,
):
    item_id = data.item_id
    updates = []
    params = []

    if data.name:
        updates.append("name = %s")
        params.append(data.name)
    if data.description:
        updates.append("description = %s")
        params.append(data.description)
    if data.price:
        updates.append("price = %s")
        params.append(data.price)

    if file:
        # Upload new image to S3
        contents = await file.read()
        file_like_object = io.BytesIO(contents)
        s3.upload_fileobj(
            Fileobj=file_like_object,
            Bucket=BUCKET_NAME,
            Key=f"menu-items/{item_id}.jpg",
        )
        updates.append("image_url = %s")
        params.append(f"https://{BUCKET_NAME}.s3.amazonaws.com/menu-items/{item_id}.jpg")

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(item_id)
    query = f"UPDATE items SET {', '.join(updates)} WHERE item_id = %s"
    db.execute(query, params)
    return {"message": "Item updated successfully"}

@app.delete("/items")
async def delete_item(
    data: DeleteItemRequest,  # Use a Pydantic model to parse the request body
    user: User = Depends(verify_admin),
    db=Depends(get_db_session),
):
    item_id = data.item_id

    # Delete image from S3
    s3.delete_object(Bucket=BUCKET_NAME, Key=f"menu-items/{item_id}.jpg")

    db.execute("DELETE FROM items WHERE item_id = %s", [item_id])
    return {"message": "Item deleted successfully"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)