import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())

__version__ = "0.1.0"
"""AT Protocol OAuth authentication client."""

import logging

# Set up null handler to prevent "No handler found" warnings
logging.getLogger(__name__).addHandler(logging.NullHandler())

# Version information
__version__ = "0.1.0"

# Public API exports
from .identity import resolve_identity
from .did import get_did_document
from .metadata import (
    get_pds_metadata,
    extract_auth_server,
    get_auth_server_metadata,
)
from .oauth import (
    generate_oauth_state,
    generate_code_verifier,
    generate_code_challenge,
    send_par_request,
)
from .security import is_safe_url
from .utils import build_auth_url

__all__ = [
    "resolve_identity",
    "get_did_document",
    "get_pds_metadata",
    "extract_auth_server",
    "get_auth_server_metadata",
    "generate_oauth_state",
    "generate_code_verifier",
    "generate_code_challenge",
    "send_par_request",
    "is_safe_url",
    "build_auth_url",
]
