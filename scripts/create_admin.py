#!/usr/bin/env python
"""Create admin user in MongoDB."""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import bcrypt
import uuid
from datetime import datetime

async def create_admin():
    client = AsyncIOMotorClient('mongodb://mongodb:27017/?directConnection=true')
    db = client['rag_parhelion']
    users = db['users']
    
    email = 'emanuel.plopu@parhelion.energy'
    # Delete existing user and recreate with new password
    await users.delete_one({'email': email})
    
    password = 'Omegat13'
    salt = bcrypt.gensalt(rounds=12)
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    user = {
        '_id': str(uuid.uuid4()),
        'email': email,
        'name': 'Emanuel Plopu',
        'password_hash': password_hash,
        'created_at': datetime.now(),
        'updated_at': datetime.now(),
        'is_active': True,
        'is_admin': True
    }
    await users.insert_one(user)
    print(f'Admin user created: {email}')
    print(f'Default password: {password}')
    print('Please change your password after login!')

if __name__ == '__main__':
    asyncio.run(create_admin())
