"""Verified HTTPS certificates, including Python installations on macOS."""
import ssl
try:
    import certifi
    TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    TLS_CONTEXT = ssl.create_default_context()
