from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional

app = FastAPI(title="My FastAPI App")

# Configure templates
templates = Jinja2Templates(directory="templates")


# Basic home route
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "home.html",
        {"request": request, "title": "Home"}
    )


# Register routes
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "title": "Register"}
    )


@app.get("/login", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "title": "Register"}
    )

# Admin routes
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    # Here you would typically:
    # 1. Check authentication
    # 2. Verify admin privileges
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "title": "Admin Panel"}
    )


# Order routes
@app.get("/order", response_class=HTMLResponse)
async def order_page(request: Request):
    return templates.TemplateResponse(
        "order.html",
        {"request": request, "title": "Place Order"}
    )



# Error handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "title": "Error",
            "message": "Page not found"
        },
        status_code=404
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)