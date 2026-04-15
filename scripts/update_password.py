"""Update a user's password in a specific database."""
import asyncio
import sys
from motor.motor_asyncio import AsyncIOMotorClient
import bcrypt

MONGODB_URI = "mongodb://mongodb:27017/?directConnection=true"
DATABASE = sys.argv[1] if len(sys.argv) > 1 else "rag_test_law"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else ""
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else ""

async def main():
    if not EMAIL or not PASSWORD:
        print("Usage: update_password.py <database> <email> <new_password>")
        sys.exit(1)
    
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client[DATABASE]
    
    salt = bcrypt.gensalt(rounds=12)
    pw_hash = bcrypt.hashpw(PASSWORD.encode('utf-8'), salt).decode('utf-8')
    
    result = await db['users'].update_one(
        {'email': EMAIL},
        {'$set': {'password_hash': pw_hash}}
    )
    
    if result.modified_count:
        print(f"Password updated for {EMAIL} in {DATABASE}")
    else:
        print(f"User {EMAIL} not found in {DATABASE}")

asyncio.run(main())
