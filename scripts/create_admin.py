#!/usr/bin/env python
"""Create or update admin/superadmin user in MongoDB.

Usage:
    python create_admin.py                     # Use defaults
    python create_admin.py --make-superadmin   # Upgrade existing user to superadmin
    MONGODB_URI=mongodb://... python create_admin.py  # Custom MongoDB connection
"""
import asyncio
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
import bcrypt
import uuid
from datetime import datetime

# Configuration - can be overridden via environment variables
MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb://mongodb:27017/?directConnection=true')
DATABASE_NAME = os.environ.get('MONGODB_DATABASE', 'rag_db')
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'emanuel.plopu@parhelion.energy')
ADMIN_NAME = os.environ.get('ADMIN_NAME', 'Emanuel Plopu')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Omegat13')


async def create_or_update_admin(make_superadmin: bool = False):
    """Create a new admin or update existing user to admin."""
    print(f"Connecting to MongoDB at: {MONGODB_URI}")
    print(f"Database: {DATABASE_NAME}")
    
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client[DATABASE_NAME]
    users = db['users']
    
    email = ADMIN_EMAIL.lower()
    
    # Check if user exists
    existing_user = await users.find_one({'email': email})
    
    if existing_user:
        if make_superadmin:
            # Just update to admin
            result = await users.update_one(
                {'email': email},
                {'$set': {'is_admin': True, 'is_active': True, 'updated_at': datetime.now()}}
            )
            print(f"Updated user {email} to superadmin status")
            print(f"User ID: {existing_user['_id']}")
            return existing_user['_id']
        else:
            # Delete and recreate with new password
            await users.delete_one({'email': email})
            print(f"Existing user deleted: {email}")
    
    # Create new admin user
    salt = bcrypt.gensalt(rounds=12)
    password_hash = bcrypt.hashpw(ADMIN_PASSWORD.encode('utf-8'), salt).decode('utf-8')
    
    user_id = str(uuid.uuid4())
    user = {
        '_id': user_id,
        'email': email,
        'name': ADMIN_NAME,
        'password_hash': password_hash,
        'created_at': datetime.now(),
        'updated_at': datetime.now(),
        'is_active': True,
        'is_admin': True  # Superadmin status
    }
    await users.insert_one(user)
    
    print(f"\n{'='*50}")
    print(f"Superadmin user created/updated successfully!")
    print(f"{'='*50}")
    print(f"  Email:    {email}")
    print(f"  Name:     {ADMIN_NAME}")
    print(f"  Password: {ADMIN_PASSWORD}")
    print(f"  User ID:  {user_id}")
    print(f"  Admin:    True (superadmin)")
    print(f"{'='*50}")
    print(f"\nPlease change your password after login!")
    print(f"Go to: /system/api-keys to create API keys for external services.")
    
    return user_id


async def verify_admin_status():
    """Verify the admin user exists and show their status."""
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client[DATABASE_NAME]
    users = db['users']
    
    email = ADMIN_EMAIL.lower()
    user = await users.find_one({'email': email})
    
    if user:
        print(f"\nUser found: {email}")
        print(f"  ID:       {user['_id']}")
        print(f"  Name:     {user.get('name', 'N/A')}")
        print(f"  Admin:    {user.get('is_admin', False)}")
        print(f"  Active:   {user.get('is_active', True)}")
        print(f"  Created:  {user.get('created_at', 'N/A')}")
        return user
    else:
        print(f"\nUser not found: {email}")
        return None


if __name__ == '__main__':
    make_superadmin = '--make-superadmin' in sys.argv or '-s' in sys.argv
    verify_only = '--verify' in sys.argv or '-v' in sys.argv
    
    if verify_only:
        asyncio.run(verify_admin_status())
    else:
        asyncio.run(create_or_update_admin(make_superadmin))
