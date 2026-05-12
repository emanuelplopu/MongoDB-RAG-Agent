#!/usr/bin/env python
"""Test script to create a backup."""
import httpx
import time

BASE_URL = "http://localhost:8000"

def main():
    client = httpx.Client(base_url=BASE_URL, timeout=600.0)
    
    # Login
    print("Logging in...")
    login = client.post('/api/v1/auth/login', json={'email': 'emanuel.plopu@parhelion.energy', 'password': 'Omegat13'})
    print(f"Login status: {login.status_code}")
    
    if login.status_code != 200:
        print(f"Login failed: {login.text}")
        return
    
    token = login.json().get('access_token')
    headers = {'Authorization': f'Bearer {token}'}
    
    # Create backup
    print("\nCreating backup (this may take a while)...")
    start = time.time()
    
    backup = client.post(
        '/api/v1/backups/create',
        json={
            'backup_type': 'full',
            'include_embeddings': True,
            'include_system_collections': True
        },
        headers=headers
    )
    
    elapsed = time.time() - start
    print(f"\nBackup request completed in {elapsed:.2f}s")
    print(f"Status: {backup.status_code}")
    print(f"Response: {backup.text[:2000] if backup.text else 'No response'}")

if __name__ == "__main__":
    main()
