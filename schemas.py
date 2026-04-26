from pydantic import BaseModel, EmailStr
from typing import Optional

# --- 1. Caretaker Signup ---
class UserSignup(BaseModel):
    firstName: str
    lastName: str
    age: int
    email: EmailStr
    password: str

# --- 2. Caretaker/Patient General Login ---
class UserLogin(BaseModel):
    email: str
    password: str
    userType: str
    caretakerEmail: Optional[EmailStr] = None

# --- 3. Patient Registration (By Caretaker) ---
# Jab caretaker patient ko add karega tab ye use hoga
class PatientSignup(BaseModel):
    name: str
    age: int
    passcode: str # Backup ke liye
    caretaker_email: EmailStr # Takay pata ho ye kis ka patient hai

# --- 4. QR Login Payload ---
# Jab patient mobile se QR scan karega toh ye data backend jayega
class QRLoginRequest(BaseModel):
    qr_token: str

class CaretakerCreate(BaseModel):
    firstName: str
    lastName: str
    age: int
    email: str
    password: str
