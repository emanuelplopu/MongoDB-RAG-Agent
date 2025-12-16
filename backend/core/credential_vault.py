"""
Credential Vault for Secure Token Storage

Provides encryption at rest for OAuth tokens, API keys, and passwords
stored in the database. Uses Fernet symmetric encryption with a master
key derived from environment configuration.

IMPORTANT: In production, consider using:
- HashiCorp Vault
- AWS Secrets Manager
- Azure Key Vault
- Google Cloud Secret Manager
"""

import os
import json
import base64
import logging
from typing import Any, Optional
from datetime import datetime
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)


class CredentialVaultError(Exception):
    """Base exception for credential vault operations."""
    pass


class EncryptionError(CredentialVaultError):
    """Error during encryption."""
    pass


class DecryptionError(CredentialVaultError):
    """Error during decryption."""
    pass


class CredentialVault:
    """
    Secure credential storage with encryption at rest.
    
    Usage:
        vault = CredentialVault()
        
        # Encrypt credentials before storing in database
        encrypted = vault.encrypt({
            "access_token": "...",
            "refresh_token": "..."
        })
        
        # Decrypt when retrieving
        credentials = vault.decrypt(encrypted)
    """
    
    # Environment variable for the master key
    MASTER_KEY_ENV = "CREDENTIAL_VAULT_KEY"
    
    # Salt for key derivation (should be stored securely, not in code)
    # In production, generate and store this separately
    SALT_ENV = "CREDENTIAL_VAULT_SALT"
    
    def __init__(self, master_key: Optional[str] = None):
        """
        Initialize the credential vault.
        
        Args:
            master_key: Optional master key. If not provided, will read from
                       environment variable CREDENTIAL_VAULT_KEY.
        """
        self._cipher: Optional[Fernet] = None
        self._initialize_cipher(master_key)
    
    def _initialize_cipher(self, master_key: Optional[str] = None):
        """Initialize the Fernet cipher with the master key."""
        # Get master key from parameter or environment
        key = master_key or os.environ.get(self.MASTER_KEY_ENV)
        
        if not key:
            # Generate a key if not provided (for development only)
            logger.warning(
                f"No {self.MASTER_KEY_ENV} set. Generating ephemeral key. "
                "This is NOT suitable for production!"
            )
            key = Fernet.generate_key().decode()
            os.environ[self.MASTER_KEY_ENV] = key
        
        # Get or generate salt
        salt = os.environ.get(self.SALT_ENV)
        if not salt:
            # Use a default salt in development
            salt = "cloud-sources-credential-vault-salt"
            logger.warning(
                f"No {self.SALT_ENV} set. Using default salt. "
                "This is NOT suitable for production!"
            )
        
        # Derive a proper key from the master key
        derived_key = self._derive_key(key, salt.encode())
        self._cipher = Fernet(derived_key)
    
    def _derive_key(self, password: str, salt: bytes) -> bytes:
        """Derive a Fernet-compatible key from a password."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key
    
    def encrypt(self, data: dict[str, Any]) -> str:
        """
        Encrypt a dictionary of credentials.
        
        Args:
            data: Dictionary containing credentials to encrypt
            
        Returns:
            Base64-encoded encrypted string
            
        Raises:
            EncryptionError: If encryption fails
        """
        if not self._cipher:
            raise EncryptionError("Cipher not initialized")
        
        try:
            # Serialize to JSON
            json_data = json.dumps(data, default=str)
            
            # Encrypt
            encrypted = self._cipher.encrypt(json_data.encode())
            
            # Return as base64 string
            return base64.urlsafe_b64encode(encrypted).decode()
            
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise EncryptionError(f"Failed to encrypt credentials: {e}")
    
    def decrypt(self, encrypted_data: str) -> dict[str, Any]:
        """
        Decrypt an encrypted credential string.
        
        Args:
            encrypted_data: Base64-encoded encrypted string
            
        Returns:
            Decrypted dictionary of credentials
            
        Raises:
            DecryptionError: If decryption fails
        """
        if not self._cipher:
            raise DecryptionError("Cipher not initialized")
        
        try:
            # Decode from base64
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode())
            
            # Decrypt
            decrypted = self._cipher.decrypt(encrypted_bytes)
            
            # Parse JSON
            return json.loads(decrypted.decode())
            
        except InvalidToken:
            logger.error("Decryption failed: Invalid token (wrong key or corrupted data)")
            raise DecryptionError("Failed to decrypt: Invalid key or corrupted data")
        except json.JSONDecodeError as e:
            logger.error(f"Decryption failed: Invalid JSON: {e}")
            raise DecryptionError(f"Failed to decrypt: Invalid data format")
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise DecryptionError(f"Failed to decrypt credentials: {e}")
    
    def encrypt_field(self, value: str) -> str:
        """
        Encrypt a single string field.
        
        Args:
            value: String to encrypt
            
        Returns:
            Base64-encoded encrypted string
        """
        return self.encrypt({"value": value})
    
    def decrypt_field(self, encrypted_data: str) -> str:
        """
        Decrypt a single encrypted field.
        
        Args:
            encrypted_data: Base64-encoded encrypted string
            
        Returns:
            Decrypted string
        """
        data = self.decrypt(encrypted_data)
        return data.get("value", "")
    
    def rotate_key(self, new_key: str, encrypted_credentials: list[str]) -> list[str]:
        """
        Rotate the encryption key for a list of credentials.
        
        Args:
            new_key: New master key to use
            encrypted_credentials: List of encrypted credential strings
            
        Returns:
            List of credentials re-encrypted with the new key
        """
        # Decrypt with old key
        decrypted = [self.decrypt(ec) for ec in encrypted_credentials]
        
        # Re-initialize with new key
        self._initialize_cipher(new_key)
        
        # Re-encrypt with new key
        return [self.encrypt(dc) for dc in decrypted]


# Global vault instance
_vault: Optional[CredentialVault] = None


def get_vault() -> CredentialVault:
    """Get the global credential vault instance."""
    global _vault
    if _vault is None:
        _vault = CredentialVault()
    return _vault


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    """Convenience function to encrypt credentials."""
    return get_vault().encrypt(credentials)


def decrypt_credentials(encrypted_data: str) -> dict[str, Any]:
    """Convenience function to decrypt credentials."""
    return get_vault().decrypt(encrypted_data)


# Token management utilities

class TokenManager:
    """
    Manages OAuth token lifecycle including refresh and expiration.
    """
    
    def __init__(self, vault: Optional[CredentialVault] = None):
        self.vault = vault or get_vault()
    
    def is_token_expired(
        self, 
        expires_at: Optional[datetime],
        buffer_seconds: int = 300
    ) -> bool:
        """
        Check if a token is expired or about to expire.
        
        Args:
            expires_at: Token expiration datetime
            buffer_seconds: Buffer before actual expiration (default 5 minutes)
            
        Returns:
            True if token is expired or will expire within buffer period
        """
        if not expires_at:
            return False
        
        from datetime import timezone
        now = datetime.now(timezone.utc) if expires_at.tzinfo else datetime.utcnow()
        
        from datetime import timedelta
        return now >= (expires_at - timedelta(seconds=buffer_seconds))
    
    def store_tokens(
        self,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        **extra
    ) -> str:
        """
        Store OAuth tokens encrypted.
        
        Args:
            access_token: OAuth access token
            refresh_token: OAuth refresh token
            expires_at: Token expiration datetime
            **extra: Additional data to store
            
        Returns:
            Encrypted token data string
        """
        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "stored_at": datetime.utcnow().isoformat(),
            **extra
        }
        return self.vault.encrypt(data)
    
    def retrieve_tokens(self, encrypted_data: str) -> dict[str, Any]:
        """
        Retrieve and decrypt OAuth tokens.
        
        Args:
            encrypted_data: Encrypted token data
            
        Returns:
            Dictionary with access_token, refresh_token, expires_at, etc.
        """
        data = self.vault.decrypt(encrypted_data)
        
        # Parse datetime fields
        if data.get("expires_at"):
            data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        if data.get("stored_at"):
            data["stored_at"] = datetime.fromisoformat(data["stored_at"])
        
        return data


# Export main classes and functions
__all__ = [
    "CredentialVault",
    "CredentialVaultError",
    "EncryptionError",
    "DecryptionError",
    "TokenManager",
    "get_vault",
    "encrypt_credentials",
    "decrypt_credentials",
]
