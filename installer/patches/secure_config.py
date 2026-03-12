"""
RecallHub Secure Configuration Manager
======================================
Encrypted configuration storage using Windows Data Protection API (DPAPI).
This module is injected during the hardened build process and provides
machine-specific encryption for sensitive configuration values.

The encryption is tied to the current Windows user and machine, making it
impossible to decrypt the configuration on a different machine.
"""

import os
import sys
import json
import base64
import hashlib
import logging
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


# Windows DPAPI imports - only available on Windows
if sys.platform == 'win32':
    try:
        import ctypes
        from ctypes import wintypes
        
        # DPAPI structures
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ('cbData', wintypes.DWORD),
                ('pbData', ctypes.POINTER(ctypes.c_char))
            ]
        
        # DPAPI functions
        _crypt32 = ctypes.windll.crypt32
        _kernel32 = ctypes.windll.kernel32
        
        CryptProtectData = _crypt32.CryptProtectData
        CryptProtectData.argtypes = [
            ctypes.POINTER(DATA_BLOB),  # pDataIn
            wintypes.LPCWSTR,           # szDataDescr
            ctypes.POINTER(DATA_BLOB),  # pOptionalEntropy
            ctypes.c_void_p,            # pvReserved
            ctypes.c_void_p,            # pPromptStruct
            wintypes.DWORD,             # dwFlags
            ctypes.POINTER(DATA_BLOB)   # pDataOut
        ]
        CryptProtectData.restype = wintypes.BOOL
        
        CryptUnprotectData = _crypt32.CryptUnprotectData
        CryptUnprotectData.argtypes = [
            ctypes.POINTER(DATA_BLOB),  # pDataIn
            ctypes.POINTER(wintypes.LPWSTR),  # ppszDataDescr
            ctypes.POINTER(DATA_BLOB),  # pOptionalEntropy
            ctypes.c_void_p,            # pvReserved
            ctypes.c_void_p,            # pPromptStruct
            wintypes.DWORD,             # dwFlags
            ctypes.POINTER(DATA_BLOB)   # pDataOut
        ]
        CryptUnprotectData.restype = wintypes.BOOL
        
        LocalFree = _kernel32.LocalFree
        LocalFree.argtypes = [ctypes.c_void_p]
        LocalFree.restype = ctypes.c_void_p
        
        DPAPI_AVAILABLE = True
    except Exception as e:
        logger.warning(f"DPAPI not available: {e}")
        DPAPI_AVAILABLE = False
else:
    DPAPI_AVAILABLE = False


@dataclass
class SecureConfigEntry:
    """Represents a single encrypted configuration entry."""
    key: str
    encrypted_value: bytes
    created_at: datetime
    last_accessed: Optional[datetime] = None
    description: str = ""


class SecureConfigManager:
    """
    Encrypted configuration storage using Windows DPAPI.
    
    This manager provides:
    - Machine-specific encryption (data can't be moved to another machine)
    - User-specific encryption (data can't be accessed by other users)
    - Automatic entropy generation for additional security
    - Secure storage of API keys, tokens, and other sensitive values
    """
    
    # DPAPI flags
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    CRYPTPROTECT_LOCAL_MACHINE = 0x04
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize the secure configuration manager.
        
        Args:
            config_path: Path to store encrypted configuration.
                        Defaults to %LOCALAPPDATA%/RecallHub/config/secrets.enc
        """
        if config_path is None:
            local_app_data = os.environ.get('LOCALAPPDATA', '')
            if local_app_data:
                config_path = Path(local_app_data) / 'RecallHub' / 'config' / 'secrets.enc'
            else:
                config_path = Path.home() / '.recallhub' / 'secrets.enc'
        
        self.config_path = config_path
        self._ensure_config_dir()
        self._entropy = self._generate_entropy()
        self._cache: dict[str, str] = {}
    
    def _ensure_config_dir(self) -> None:
        """Ensure the configuration directory exists with proper permissions."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
    
    def _generate_entropy(self) -> bytes:
        """
        Generate additional entropy for DPAPI encryption.
        Uses a combination of machine-specific values.
        """
        # Combine machine-specific identifiers for entropy
        entropy_sources = [
            os.environ.get('COMPUTERNAME', ''),
            os.environ.get('USERNAME', ''),
            str(self.config_path.parent),
            'RecallHub-Entropy-v1'
        ]
        combined = '|'.join(entropy_sources)
        return hashlib.sha256(combined.encode()).digest()
    
    def _create_blob(self, data: bytes) -> DATA_BLOB:
        """Create a DATA_BLOB structure from bytes."""
        blob = DATA_BLOB()
        blob.cbData = len(data)
        blob.pbData = ctypes.cast(
            ctypes.create_string_buffer(data, len(data)),
            ctypes.POINTER(ctypes.c_char)
        )
        return blob
    
    def encrypt_value(self, plaintext: str) -> bytes:
        """
        Encrypt a string value using Windows DPAPI.
        
        Args:
            plaintext: The string to encrypt
            
        Returns:
            Encrypted bytes
            
        Raises:
            RuntimeError: If DPAPI is not available or encryption fails
        """
        if not DPAPI_AVAILABLE:
            raise RuntimeError("DPAPI is not available on this platform")
        
        data_in = self._create_blob(plaintext.encode('utf-8'))
        entropy_in = self._create_blob(self._entropy)
        data_out = DATA_BLOB()
        
        success = CryptProtectData(
            ctypes.byref(data_in),
            "RecallHub Configuration",
            ctypes.byref(entropy_in),
            None,
            None,
            self.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(data_out)
        )
        
        if not success:
            error_code = ctypes.get_last_error()
            raise RuntimeError(f"DPAPI encryption failed with error code: {error_code}")
        
        try:
            encrypted = ctypes.string_at(data_out.pbData, data_out.cbData)
            return encrypted
        finally:
            LocalFree(data_out.pbData)
    
    def decrypt_value(self, encrypted: bytes) -> str:
        """
        Decrypt bytes using Windows DPAPI.
        
        Args:
            encrypted: The encrypted bytes
            
        Returns:
            Decrypted string
            
        Raises:
            RuntimeError: If DPAPI is not available or decryption fails
        """
        if not DPAPI_AVAILABLE:
            raise RuntimeError("DPAPI is not available on this platform")
        
        data_in = self._create_blob(encrypted)
        entropy_in = self._create_blob(self._entropy)
        data_out = DATA_BLOB()
        description = wintypes.LPWSTR()
        
        success = CryptUnprotectData(
            ctypes.byref(data_in),
            ctypes.byref(description),
            ctypes.byref(entropy_in),
            None,
            None,
            self.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(data_out)
        )
        
        if not success:
            error_code = ctypes.get_last_error()
            raise RuntimeError(f"DPAPI decryption failed with error code: {error_code}")
        
        try:
            decrypted = ctypes.string_at(data_out.pbData, data_out.cbData)
            return decrypted.decode('utf-8')
        finally:
            LocalFree(data_out.pbData)
    
    def encrypt_secrets(self, secrets: dict[str, str]) -> bytes:
        """
        Encrypt a dictionary of secrets.
        
        Args:
            secrets: Dictionary of key-value pairs to encrypt
            
        Returns:
            Encrypted bytes containing all secrets
        """
        # Serialize to JSON
        json_data = json.dumps(secrets, sort_keys=True)
        return self.encrypt_value(json_data)
    
    def decrypt_secrets(self, encrypted: bytes) -> dict[str, str]:
        """
        Decrypt bytes back to a dictionary of secrets.
        
        Args:
            encrypted: Encrypted bytes
            
        Returns:
            Dictionary of decrypted secrets
        """
        json_data = self.decrypt_value(encrypted)
        return json.loads(json_data)
    
    def save_secrets(self, secrets: dict[str, str]) -> None:
        """
        Save encrypted secrets to the configuration file.
        
        Args:
            secrets: Dictionary of secrets to save
        """
        encrypted = self.encrypt_secrets(secrets)
        
        # Store with a header for version identification
        header = b'RECALLHUB_SECRETS_V1\x00'
        
        with open(self.config_path, 'wb') as f:
            f.write(header)
            f.write(base64.b64encode(encrypted))
        
        # Update cache
        self._cache = secrets.copy()
        
        logger.info(f"Saved {len(secrets)} encrypted secrets to {self.config_path}")
    
    def load_secrets(self) -> dict[str, str]:
        """
        Load and decrypt secrets from the configuration file.
        
        Returns:
            Dictionary of decrypted secrets
        """
        if not self.config_path.exists():
            logger.warning(f"Secrets file not found: {self.config_path}")
            return {}
        
        with open(self.config_path, 'rb') as f:
            content = f.read()
        
        # Verify header
        expected_header = b'RECALLHUB_SECRETS_V1\x00'
        if not content.startswith(expected_header):
            raise RuntimeError("Invalid secrets file format")
        
        encrypted_b64 = content[len(expected_header):]
        encrypted = base64.b64decode(encrypted_b64)
        
        secrets = self.decrypt_secrets(encrypted)
        
        # Update cache
        self._cache = secrets.copy()
        
        logger.info(f"Loaded {len(secrets)} secrets from {self.config_path}")
        return secrets
    
    def get_credential(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get a single credential by key.
        
        Args:
            key: The credential key
            default: Default value if key not found
            
        Returns:
            The credential value or default
        """
        # Check cache first
        if key in self._cache:
            return self._cache[key]
        
        # Load from file if cache is empty
        if not self._cache:
            try:
                self.load_secrets()
            except Exception as e:
                logger.error(f"Failed to load secrets: {e}")
                return default
        
        return self._cache.get(key, default)
    
    def set_credential(self, key: str, value: str) -> None:
        """
        Set a single credential.
        
        Args:
            key: The credential key
            value: The credential value
        """
        # Load existing secrets
        if not self._cache:
            try:
                self.load_secrets()
            except Exception:
                pass
        
        # Update and save
        self._cache[key] = value
        self.save_secrets(self._cache)
    
    def delete_credential(self, key: str) -> bool:
        """
        Delete a credential.
        
        Args:
            key: The credential key to delete
            
        Returns:
            True if deleted, False if key didn't exist
        """
        # Load existing secrets
        if not self._cache:
            try:
                self.load_secrets()
            except Exception:
                return False
        
        if key not in self._cache:
            return False
        
        del self._cache[key]
        self.save_secrets(self._cache)
        return True
    
    def list_keys(self) -> list[str]:
        """
        List all credential keys (without values).
        
        Returns:
            List of credential keys
        """
        if not self._cache:
            try:
                self.load_secrets()
            except Exception:
                return []
        
        return list(self._cache.keys())
    
    def clear_cache(self) -> None:
        """Clear the in-memory cache of secrets."""
        self._cache.clear()
    
    def is_available(self) -> bool:
        """Check if DPAPI encryption is available."""
        return DPAPI_AVAILABLE


class FallbackConfigManager:
    """
    Fallback configuration manager for non-Windows platforms.
    Uses file-based storage with base64 encoding (NOT secure - for dev only).
    """
    
    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = Path.home() / '.recallhub' / 'secrets.json'
        self.config_path = config_path
        self._cache: dict[str, str] = {}
        logger.warning("Using fallback config manager - NOT SECURE for production!")
    
    def save_secrets(self, secrets: dict[str, str]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(secrets, f)
        self._cache = secrets.copy()
    
    def load_secrets(self) -> dict[str, str]:
        if not self.config_path.exists():
            return {}
        with open(self.config_path, 'r') as f:
            self._cache = json.load(f)
        return self._cache.copy()
    
    def get_credential(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if not self._cache:
            self.load_secrets()
        return self._cache.get(key, default)
    
    def set_credential(self, key: str, value: str) -> None:
        if not self._cache:
            self.load_secrets()
        self._cache[key] = value
        self.save_secrets(self._cache)
    
    def is_available(self) -> bool:
        return True


def get_config_manager(config_path: Optional[Path] = None) -> SecureConfigManager | FallbackConfigManager:
    """
    Get the appropriate configuration manager for the current platform.
    
    Returns:
        SecureConfigManager on Windows, FallbackConfigManager otherwise
    """
    if DPAPI_AVAILABLE:
        return SecureConfigManager(config_path)
    else:
        return FallbackConfigManager(config_path)


# Singleton instance for global access
_config_manager: Optional[SecureConfigManager | FallbackConfigManager] = None


def get_global_config_manager() -> SecureConfigManager | FallbackConfigManager:
    """Get the global configuration manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = get_config_manager()
    return _config_manager
