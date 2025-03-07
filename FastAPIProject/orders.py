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
from typing import Dict


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


# Models
class CartItem(BaseModel):
    product_id: str
    quantity: int


class OrderStatus(BaseModel):
    order_id: UUID
    status: str  # pending/prepared


class OrderResponse(BaseModel):
    order_id: UUID
    customer_id: UUID
    products: Dict[str, int]
    total_price: Optional[float]
    status: str
    created_at: datetime


class DiscountCreate(BaseModel):
    discount_code: str
    discount_percentage: int
    expires_at: Optional[datetime] = None



class DiscountResponse(BaseModel):
    discount_id: UUID
    discount_code: str
    discount_percentage: int
    created_at: datetime
    expires_at: datetime


class DiscountDeleteRequest(BaseModel):
    discount_code: str


@app.post("/add_to_cart", response_model=Dict[str, str])
async def add_to_cart(
    item: CartItem,
    db=Depends(get_db_session),
    customer_id: UUID = Depends(get_current_user)
) -> dict:
    # Extract customer id from the JWT-provided user information

    print(f"Received request - Product ID: {item.product_id}, Quantity: {item.quantity}")  # Debug

    if item.quantity <= 0:
        print(f"Invalid quantity: {item.quantity}")  # Debug
        raise HTTPException(
            status_code=400,
            detail="Quantity must be greater than 0"
        )

    try:
        print(f"Checking cart for customer: {customer_id}")  # Debug
        query = "SELECT order_id, products FROM orders WHERE customer_id = %s AND status = 'cart' ALLOW FILTERING"
        cart_result = db.execute(query, [customer_id])
        cart = cart_result.one() if cart_result else None
        print(f"Cart details fetched from database: {cart}")  # Debug

        if cart:
            print(f"Existing cart found - Order ID: {cart.order_id}")  # Debug
            order_id = cart.order_id


            current_products = cart.products or {}
            print(f"Current products in cart: {current_products}")  # Debug

            # If the product already exists in the cart, update its quantity; otherwise, add it
            if item.product_id in current_products:
                current_products[item.product_id] += item.quantity
            else:
                current_products[item.product_id] = item.quantity

            print(f"Updated products map: {current_products}")  # Debug

            update_query = """
                UPDATE orders 
                SET products = %s
                WHERE customer_id = %s AND order_id = %s
            """
            print("Update query:" + update_query)
            db.execute(update_query, [current_products, customer_id, order_id])
        else:
            print("Creating new cart")  # Debug
            order_id = uuid4()
            products = {item.product_id: item.quantity}
            print(f"New cart - Order ID: {order_id}, Products: {products}")  # Debug

            insert_query = """
                           INSERT INTO orders (
                               customer_id, 
                               order_id, 
                               products, 
                               created_at,
                               status,
                               total_price,
                               address,
                               delivery_method,
                               payment_method,
                               discount
                           ) VALUES (%s, %s, %s, %s, 'cart', 0, 'none', 'none', 'none', 0)
                       """
            print(f"Executing query: {insert_query}")  # Debug
            db.execute(insert_query, [
                customer_id,
                order_id,
                products,
                datetime.utcnow()
            ])

        response_data = {
            "message": "Product added to cart successfully",
            "order_id": str(order_id),
            "product_id": item.product_id,
            "quantity": str(item.quantity)
        }
        print(f"Success response: {response_data}")  # Debug
        return response_data

    except Exception as e:
        print(f"Error occurred: {str(e)}")  # Debug
        raise HTTPException(
            status_code=500,
            detail=f"Failed to add product to cart: {str(e)}"
        )



@app.post("/update_cart")
async def update_cart(
        item: CartItem,
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    try:
        print(f"Checking cart for customer: {current_user}")  # Debug
        query = "SELECT order_id, products, order_id FROM orders WHERE customer_id = %s AND status = 'cart' ALLOW FILTERING"
        cart_result = session.execute(query, [current_user])
        cart = cart_result.one() if cart_result else None
        print(f"Cart details fetched from database: {cart}")

        if not cart:
            raise HTTPException(status_code=404, detail="Cart not found")
        print("Checking item's quantity: ", item.quantity)
        products = dict(cart.products)
        if item.quantity <= 0:
            products.pop(item.product_id, None)
        else:
            products[item.product_id] = item.quantity

        print("Updated products: ", products)

        query = "UPDATE orders SET products = %s WHERE customer_id = %s AND order_id = %s"
        session.execute(query, (products, current_user, cart.order_id))

        return {"message": "Cart updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/remove_from_cart")
async def remove_from_cart(
        item: CartItem,
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    try:

        print(f"Checking cart for customer: {current_user}")  # Debug
        query = "SELECT order_id, products, order_id FROM orders WHERE customer_id = %s AND status = 'cart' ALLOW FILTERING"
        cart_result = session.execute(query, [current_user])
        cart = cart_result.one() if cart_result else None
        print(f"Cart details fetched from database: {cart}")

        if not cart:
            raise HTTPException(status_code=404, detail="Cart not found")

        products = dict(cart.products)
        products.pop(item.product_id, None)

        query = "UPDATE orders SET products = %s WHERE customer_id = %s AND order_id = %s"
        session.execute(query, (products, current_user, cart.order_id))

        return {"message": "Item removed from cart successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/get_cart")
async def get_cart(
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    try:
        print(f"Checking cart for customer: {current_user}")  # Debug
        query = "SELECT * FROM orders WHERE customer_id = %s AND status = 'cart' ALLOW FILTERING"
        cart_result = session.execute(query, [current_user])
        cart = cart_result.one() if cart_result else None
        print(f"Cart details fetched from database: {cart}")

        if not cart:
            return {"message": "Cart is empty", "items": {}}

        return OrderResponse(
            order_id=cart.order_id,
            customer_id=cart.customer_id,
            products=cart.products,
            total_price=cart.total_price,
            status=cart.status,
            created_at=cart.created_at
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# @app.post("/clear_cart")
# async def clear_cart(
#         current_user: dict = Depends(get_current_user),
#         session=Depends(get_db_session)
# ):
#     try:
#         query = "SELECT order_id FROM orders WHERE customer_id = %s"
#         result = session.execute(query, (current_user['customer_id'],))
#         cart = next((order for order in result if order.status == 'cart'), None)
#
#         if cart:
#             query = "DELETE FROM orders WHERE customer_id = %s AND order_id = %s"
#             session.execute(query, (current_user['customer_id'], cart.order_id))
#
#         return {"message": "Cart cleared successfully"}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))


@app.get("/get_pending_orders")
async def get_pending_orders(
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    try:
        # Fetch worker status for the current user
        user_query = "SELECT worker FROM customers WHERE customer_id = %s"
        user_result = session.execute(user_query, [current_user]).one()


        if not user_result or  user_result.worker !=1:
            raise HTTPException(status_code=403, detail="Not authorized")

        query = "SELECT * FROM orders WHERE status = 'pending' ALLOW FILTERING"
        result = session.execute(query)

        return [OrderResponse(**order) for order in result]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/get_user_pending_orders")
async def get_user_pending_orders(
        current_user: dict = Depends(get_current_user),
        session=Depends(get_db_session)
):
    try:
        query = "SELECT * FROM orders WHERE customer_id = %s"
        result = session.execute(query, (current_user['customer_id'],))
        pending_orders = [order for order in result if order.status == 'pending']

        return [OrderResponse(**order) for order in pending_orders]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/update_order_status")
async def update_order_status(
        order_status: OrderStatus,
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    print("HEllo")
    print(order_status)
    try:
        user_query = "SELECT worker FROM customers WHERE customer_id = %s"
        user_result = session.execute(user_query, [current_user]).one()

        if not user_result or user_result.worker != 1:
            raise HTTPException(status_code=403, detail="Not authorized")

        user_query = "SELECT order_id FROM orders WHERE order_id = %s "
        user_result = session.execute(user_query, (order_status.order_id,)).one()
        if not user_result:
            raise HTTPException(status_code=404, detail="Order not found")

        query = "UPDATE orders SET status = %s WHERE order_id = %s"
        session.execute(query, (order_status.status, order_status.order_id,))

        return {"message": f"Order status updated to {order_status.status}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/get_prepared_orders")
async def get_prepared_orders(
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    try:
        user_query = "SELECT worker FROM customers WHERE customer_id = %s"
        user_result = session.execute(user_query, [current_user]).one()

        if not user_result or user_result.worker != 1:
            raise HTTPException(status_code=403, detail="Not authorized")

        query = "SELECT * FROM orders WHERE status = 'prepared' ALLOW FILTERING"
        result = session.execute(query)

        return [OrderResponse(**order._asdict()) for order in result]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/get_user_prepared_orders")
async def get_user_prepared_orders(
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    try:
        query = "SELECT * FROM orders WHERE customer_id = %s ALLOW FILTERING"
        result = session.execute(query, (current_user,))
        prepared_orders = [order for order in result if order.status == 'prepared']

        return [OrderResponse(**order._asdict()) for order in prepared_orders]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create_discount")
async def create_discount_code(
        discount: DiscountCreate,
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    # Check if user is admin
    user_query = "SELECT admin FROM customers WHERE customer_id = %s"
    user_result = session.execute(user_query, [current_user]).one()

    if not user_result or user_result.admin != 1:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Check if discount code already exists
    query = "SELECT * FROM discounts WHERE discount_code = %s ALLOW FILTERING"
    existing_discount = session.execute(query, [discount.discount_code]).one()
    if existing_discount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount code already exists"
        )

    discount_id = uuid4()
    created_at = datetime.utcnow()
    expired_at = created_at + timedelta(days=30)
    # Insert new discount code
    query = """
        INSERT INTO discounts (
            discount_id, created_by, discount_code, created_at, 
            discount_percentage, expires_at
        ) VALUES (%s, %s, %s, %s, %s, %s)
    """
    session.execute(query, [
        discount_id,
        current_user,
        discount.discount_code,
        created_at,
        discount.discount_percentage,
        expired_at
    ])

    return DiscountResponse(
        discount_id=discount_id,
        discount_code=discount.discount_code,
        discount_percentage=discount.discount_percentage,
        created_at=created_at,
        expires_at=expired_at
    )

@app.delete("/delete_discount_code")
async def delete_discount_code_admin(
        request: DiscountDeleteRequest,
        current_user: UUID = Depends(get_current_user),
        session=Depends(get_db_session)
):
    discount_code = request.discount_code
    # Check if user is admin
    user_query = "SELECT admin FROM customers WHERE customer_id = %s"
    user_result = session.execute(user_query, [current_user]).one()

    if not user_result or user_result.admin != 1:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Check if discount exists
    query = "SELECT * FROM discounts WHERE discount_code = %s ALLOW FILTERING"
    discount = session.execute(query, [discount_code]).one()
    if not discount:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discount code not found"
        )

    # Delete the discount code
    query = "DELETE FROM discounts WHERE discount_id = %s"
    session.execute(query, [discount.discount_id])

    return {"message": f"Discount code '{discount_code}' has been deleted"}



def delete_expired_discounts(db):
    query = "SELECT * FROM discounts"
    discounts = db.execute(query)

    for discount in discounts:
        if discount.expires_at < datetime.utcnow():
            delete_query = "DELETE FROM discounts WHERE discount_id = %s"
            db.execute(delete_query, [discount.discount_id])


@app.get("/apply_discounts")
async def apply_discount_code(
        discount_code: str,
        current_user: UUID = Depends(get_current_user),
        db=Depends(get_db_session)
):
    # Remove expired discounts from the database
    delete_expired_discounts(db)

    # Get discount details
    query = "SELECT * FROM discounts WHERE discount_code = %s ALLOW FILTERING"
    discount = db.execute(query, [discount_code]).one()

    if not discount:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid discount code"
        )

    # Get the user's cart UUID
    query = "SELECT * FROM orders WHERE customer_id = %s AND status = 'cart' ALLOW FILTERING"
    cart_result = db.execute(query, [current_user]).one()


    if not cart_result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart not found"
        )

    if cart_result.discount != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A discount is already applied to this cart"
        )

    cart_total = cart_result.total_price
    # Calculate discounted amount and final price
    discount_amount = (cart_total * discount.discount_percentage) / 100
    final_price = cart_total - discount_amount

    # Update the database with the discount and updated total price
    update_query = """
        UPDATE orders 
        SET discount = %s, total_price = %s 
        WHERE order_id = %s
    """
    db.execute(update_query, [discount.discount_percentage, final_price, cart_result.order_id])

    return {
        "original_price": cart_total,
        "discount_percentage": discount.discount_percentage,
        "discount_amount": discount_amount,
        "final_price": final_price
    }



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)