from authlib.jose import JsonWebKey
from dotenv import load_dotenv
import json
import re
import httpx
import logging
import secrets
import base64
import os

# Set up logging configuration
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),  # Output to console
        logging.FileHandler("app.log"),  # Output to file
    ],
)
logger = logging.getLogger(__name__)

# jclient_secret_jwk_str = os.getenv("CLIENT_SECRET_JWK") or exit(
#     "Missing CLIENT_SECRET_JWK"
# )
#
# CLIENT_SECRET_JWK = JsonWebKey.import_key(client_secret_jwk_str)
# CLIENT_PUB_JWK = json.loads(CLIENT_SECRET_JWK.as_json(is_private=False))
# assert "d" not in CLIENT_PUB_JWK


DOMAIN_RE = re.compile(
    r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z][a-zA-Z0-9-]*[a-zA-Z]$"
)

HANDLE_REGEX = re.compile(
    r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)"
    r"+[a-zA-Z]"
    r"([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)

DID_RE = re.compile(
    r"^did:"  # Required prefix
    r"[a-z]+:"  # method-name (lowercase only)
    r"[a-zA-Z0-9._%-]{1,2048}"  # method-specific-id with length limit
    r"(?<!:)$"  # Cannot end with colon
)


def resolve_identity(username: str):
    """
    Resolve a username (handle or DID) to a DID.

    Args:
        username: A string that could be a handle or DID

    Returns:
        The DID if resolution is successful, None otherwise
    """
    if re.match(HANDLE_REGEX, username):
        # Handle the case where username is a handle
        logging.debug(f"Username is a handle: {username}")

        # Extract domain and TLD from the handle
        parts = username.split(".")
        if len(parts) >= 2:
            domain_tld = ".".join(parts[1:])
            logging.info(f"Extracted domain and TLD: {domain_tld}")
        else:
            logging.warning(f"Could not extract domain from handle: {username}")
            return None

        url = f"https://{domain_tld}/xrpc/com.atproto.identity.resolveHandle?handle={username}"

        # Make HTTP request to resolve handle to DID
        try:
            response = httpx.get(url)
            response.raise_for_status()  # Raise exception for 4XX/5XX responses

            # Parse the JSON response
            data = response.json()

            # Extract the DID from the response
            did = data.get("did")
            if did:
                logging.debug(f"Resolved handle {username} to DID: {did}")
                return did
            else:
                logging.info(
                    f"Failed to resolve handle: {username} No DID found in response"
                )
                return None
        except httpx.HTTPStatusError as e:
            logging.info(f"HTTP error occurred while resolving handle: {e}")
            return None
        except httpx.RequestError as e:
            logging.info(f"Request error occurred while resolving handle: {e}")
            return None
        except json.JSONDecodeError:
            logging.info("Failed to parse JSON response from handle resolution")
            return None
    elif re.match(DID_RE, username):
        # If the username is already a DID, return it directly
        logging.info(f"Username is already a DID: {username}")
        return username
    else:
        logging.warning(f"Username '{username}' is neither a valid handle nor a DID")
        return None


def get_did_document(did):
    """
    Retrieve the DID document for a given DID.

    Args:
        did: The DID to retrieve the document for

    Returns:
        The DID document as a dictionary if successful, None otherwise
    """
    url = f"https://plc.directory/{did}"

    try:
        # Make HTTP request to retrieve the DID document
        response = httpx.get(url)
        response.raise_for_status()

        # Parse the JSON response
        did_document = response.json()
        logging.info(f"Retrieved DID document for {did}")

        # Extract the PDS URL from the DID document
        if "service" in did_document and len(did_document["service"]) > 0:
            pds_url = did_document["service"][0].get("serviceEndpoint")
            if pds_url:
                logging.info(f"User's PDS URL: {pds_url}")
                return did_document, pds_url

        logging.warning(f"Could not find PDS URL in DID document for {did}")
        return did_document, None
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logging.warning(f"DID not found: {did}")
            return None, None
        elif e.response.status_code == 410:
            logging.warning(f"DID not available (tombstone) 🪦: {did}")
            return None, None
        else:
            logging.error(f"HTTP error occurred while retrieving DID document: {e}")
            return None, None
    except httpx.RequestError as e:
        logging.error(f"Request error occurred while retrieving DID document: {e}")
        return None, None
    except json.JSONDecodeError:
        logging.error("Failed to parse JSON response from DID document retrieval")
        return None, None


def get_pds_metadata(pds_url):
    """
    Retrieve the OAuth protected resource metadata from the PDS server.

    Args:
        pds_url: The URL of the PDS server

    Returns:
        The metadata as a dictionary if successful, None otherwise
    """
    if not pds_url:
        logging.error("Cannot get PDS metadata: PDS URL is None")
        return None

    metadata_url = f"{pds_url.rstrip('/')}/.well-known/oauth-protected-resource"
    logging.info(f"Fetching PDS metadata from: {metadata_url}")

    try:
        response = httpx.get(metadata_url)
        response.raise_for_status()

        metadata = response.json()
        logging.info(f"Successfully retrieved PDS metadata")
        return metadata
    except httpx.HTTPStatusError as e:
        logging.error(f"HTTP error occurred while retrieving PDS metadata: {e}")
        return None
    except httpx.RequestError as e:
        logging.error(f"Request error occurred while retrieving PDS metadata: {e}")
        return None
    except json.JSONDecodeError:
        logging.error("Failed to parse JSON response from PDS metadata retrieval")
        return None


def extract_auth_server(metadata):
    """
    Extract the authorization server URL from the PDS metadata.

    Args:
        metadata: The PDS metadata dictionary

    Returns:
        The authorization server URL if found, None otherwise
    """
    if not metadata:
        logging.error("Cannot extract authorization server: Metadata is None")
        return None

    auth_servers = metadata.get("authorization_servers")
    if not auth_servers or not isinstance(auth_servers, list) or len(auth_servers) == 0:
        logging.error("No authorization servers found in metadata")
        return None

    # Use the first authorization server in the list
    return auth_servers


def get_auth_server_metadata(auth_servers):
    """
    Retrieve the OAuth authorization server metadata from the first available server.

    Args:
        auth_servers: List of authorization server URLs

    Returns:
        A tuple containing (metadata, auth_endpoint, token_endpoint, par_endpoint)
        All values will be None if no server is available
    """
    if not auth_servers or not isinstance(auth_servers, list):
        logging.error(
            "Cannot get auth server metadata: No authorization servers provided"
        )
        return None, None, None, None

    for auth_server in auth_servers:
        metadata_url = (
            f"{auth_server.rstrip('/')}/.well-known/oauth-authorization-server"
        )
        logging.info(f"Trying to fetch auth server metadata from: {metadata_url}")

        try:
            response = httpx.get(metadata_url)
            response.raise_for_status()

            metadata = response.json()
            logging.info(
                f"Successfully retrieved auth server metadata from {auth_server}"
            )

            # Extract endpoints from metadata
            auth_endpoint = metadata.get("authorization_endpoint")
            token_endpoint = metadata.get("token_endpoint")
            par_endpoint = metadata.get("pushed_authorization_request_endpoint")

            if auth_endpoint and token_endpoint:
                logging.info(f"Found authorization endpoint: {auth_endpoint}")
                logging.info(f"Found token endpoint: {token_endpoint}")
                if par_endpoint:
                    logging.info(f"Found PAR endpoint: {par_endpoint}")
                else:
                    logging.warning("PAR endpoint not found in auth server metadata")

                return metadata, auth_endpoint, token_endpoint, par_endpoint
            else:
                logging.warning(
                    f"Missing required endpoints in auth server metadata from {auth_server}"
                )
                continue

        except httpx.HTTPStatusError as e:
            logging.warning(
                f"HTTP error occurred while retrieving auth server metadata from {auth_server}: {e}"
            )
            continue
        except httpx.RequestError as e:
            logging.warning(
                f"Request error occurred while retrieving auth server metadata from {auth_server}: {e}"
            )
            continue
        except json.JSONDecodeError:
            logging.warning(
                f"Failed to parse JSON response from auth server metadata retrieval from {auth_server}"
            )
            continue

    logging.error("Failed to retrieve metadata from any authorization server")
    return None, None, None, None


def generate_oauth_state():
    """
    Generate a secure random state value for OAuth requests.

    The state value is a random string that is:
    - Unpredictable and unique for each authorization request
    - At least 32 bytes (converted to a hex string)
    - Used as a CSRF protection mechanism

    Returns:
        A secure random string to use as the state parameter
    """

    # Generate 32 bytes of random data and convert to hex
    # This will result in a 64-character hex string
    state = secrets.token_hex(32)
    logging.info(f"Generated OAuth state parameter ({len(state)} characters)")
    return state


def generate_code_verifier(length=128):
    """
    Generate a code_verifier for PKCE (Proof Key for Code Exchange) in OAuth.

    The code_verifier is:
    - A cryptographically random string between 43 and 128 characters
    - Contains only unreserved URL characters: A-Z, a-z, 0-9, hyphen (-),
      period (.), underscore (_), and tilde (~)

    Args:
        length: Length of the code verifier (default: 128)
               Must be between 43 and 128 characters

    Returns:
        A secure random string to use as the code_verifier parameter
    """
    if length < 43 or length > 128:
        raise ValueError("Code verifier length must be between 43 and 128 characters")

    # Generate random bytes and convert to base64
    # Generate random bytes (3/4 of the desired length to account for base64 expansion)
    random_bytes = secrets.token_bytes(length * 3 // 4)

    # Convert to base64 and remove padding
    code_verifier = base64.urlsafe_b64encode(random_bytes).decode("utf-8").rstrip("=")

    # Trim to desired length
    code_verifier = code_verifier[:length]

    logging.info(f"Generated code_verifier ({len(code_verifier)} characters)")
    return code_verifier


def generate_code_challenge(code_verifier):
    """
    Generate a code_challenge from a code_verifier for PKCE in OAuth.

    The code_challenge is:
    - The SHA-256 hash of the code_verifier
    - Base64URL-encoded

    Args:
        code_verifier: The code_verifier string

    Returns:
        The code_challenge string
    """
    import hashlib

    # Apply SHA-256 hash to the code_verifier
    code_verifier_bytes = code_verifier.encode("ascii")
    hash_bytes = hashlib.sha256(code_verifier_bytes).digest()

    # Base64URL-encode the hash
    code_challenge = base64.urlsafe_b64encode(hash_bytes).decode("utf-8").rstrip("=")

    logging.info(f"Generated code_challenge ({len(code_challenge)} characters)")
    return code_challenge


def send_par_request(
    par_endpoint,
    code_challenge,
    state,
    login_hint=None,
    client_id=None,
    redirect_uri=None,
    scope="atproto transition:generic",
):
    """
    Send a Pushed Authorization Request (PAR) to the authorization server.

    Args:
        par_endpoint: The PAR endpoint URL from the authorization server metadata
        code_challenge: The PKCE code challenge generated from the code verifier
        state: The OAuth state parameter for CSRF protection
        login_hint: Optional handle or DID to pre-fill the login form
        client_id: The OAuth client ID (URL to client metadata)
        redirect_uri: The callback URL where the authorization code will be sent
        scope: The requested OAuth scopes

    Returns:
        A tuple containing (request_uri, expires_in) if successful, (None, None) otherwise
    """
    if not par_endpoint:
        logging.error("Cannot send PAR request: PAR endpoint is None")
        return None, None

    # Prepare the request parameters
    params = {
        "response_type": "code",
        "code_challenge_method": "S256",
        "scope": scope,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "state": state,
    }

    # Add login_hint if provided
    if login_hint:
        params["login_hint"] = login_hint

    logging.info(f"Sending PAR request to: {par_endpoint}")
    logging.debug(f"PAR request parameters: {params}")

    try:
        # Send the POST request with form-encoded body
        response = httpx.post(
            par_endpoint,
            data=params,
        )
        response.raise_for_status()

        # Parse the JSON response
        data = response.json()
        logging.info("PAR request successful")

        # Extract the request_uri and expires_in values
        request_uri = data.get("request_uri")
        expires_in = data.get("expires_in")

        if request_uri:
            logging.info(f"Received request_uri: {request_uri}")
            logging.info(f"Request URI expires in: {expires_in} seconds")
            return request_uri, expires_in
        else:
            logging.error("No request_uri found in PAR response")
            return None, None

    except httpx.HTTPStatusError as e:
        logging.error(f"HTTP error occurred during PAR request: {e}")
        try:
            # Try to extract error details from response
            error_data = e.response.json()
            logging.error(f"Error details: {error_data}")
        except:
            pass
        return None, None
    except httpx.RequestError as e:
        logging.error(f"Request error occurred during PAR request: {e}")
        return None, None
    except json.JSONDecodeError:
        logging.error("Failed to parse JSON response from PAR request")
        return None, None


# Main execution flow


def main():
    load_dotenv()
    """Main execution flow for the OAuth authentication process."""
    # 1) get users handle
    # Login can start with a handle, DID, or auth server URL
    username = os.getenv("USERNAME") or exit("Missing USERNAME")
    app_url = os.getenv("APP_URL") or exit("Missing APP_URL")
    logging.info(f"Starting OAuth flow for username: {username}")

    # 2) retrieve the users DID
    user_did = resolve_identity(username)
    if not user_did:
        logging.error(f"Failed to resolve username {username} to a DID")
        return False

    logging.info(f"Resolved username {username} to DID: {user_did}")

    # 3) retrieve the user DID document
    # 4) get the URL of the PDS server from the DID doc
    did_document, pds_url = get_did_document(user_did)
    if not did_document or not pds_url:
        logging.error(f"Failed to retrieve DID document or PDS URL for {user_did}")
        return False

    logging.info(f"Successfully retrieved DID document for {user_did}")

    # 5) get the PDS server metadata from the well-known endpoint
    pds_metadata = get_pds_metadata(pds_url)
    if not pds_metadata:
        logging.error("Failed to retrieve PDS metadata")
        return False

    logging.info("PDS metadata retrieved successfully")

    # 6) from the metadata extract the authorization server
    auth_servers = extract_auth_server(pds_metadata)
    if not auth_servers:
        logging.error("Failed to extract authorization server from metadata")
        return False

    logging.info(f"Authorization server URL: {auth_servers[0]}")

    # 7) get the metadata of the authorization server
    auth_metadata, auth_endpoint, token_endpoint, par_endpoint = (
        get_auth_server_metadata(auth_servers)
    )

    if not auth_metadata:
        logging.error("Failed to retrieve auth server metadata from any server")
        return False

    logging.info("Auth server metadata retrieved successfully")
    print("Auth Server Endpoints:")
    print(f"  Authorization: {auth_endpoint}")
    print(f"  Token: {token_endpoint}")
    print(f"  PAR: {par_endpoint or 'Not available'}")

    # Generate a state parameter for OAuth request
    oauth_state = generate_oauth_state()
    print(f"Generated OAuth state: {oauth_state[:10]}... (truncated)")

    # Generate a code_verifier for PKCE
    code_verifier = generate_code_verifier()
    print(f"Generated code_verifier: {code_verifier[:10]}... (truncated)")

    # Generate a code_challenge from the code_verifier
    code_challenge = generate_code_challenge(code_verifier)
    print(f"Generated code_challenge: {code_challenge[:10]}... (truncated)")

    # In a real application, you would store these values
    # to use them when exchanging the authorization code for tokens

    # Send the PAR request if we have a PAR endpoint
    if par_endpoint:
        # Use the username as login_hint if available
        request_uri, expires_in = send_par_request(
            par_endpoint=par_endpoint,
            code_challenge=code_challenge,
            state=oauth_state,
            login_hint=username,
            client_id=f"https://{app_url}/oauth/client-metadata.json",
            redirect_uri=f"https://{app_url}/oauth/callback/",
        )

        if request_uri:
            print("PAR request successful!")
            print(f"Request URI: {request_uri}")
            print(f"Expires in: {expires_in} seconds")

            # Construct the authorization URL
            auth_url = f"{auth_endpoint}?\
            client_id=https://madrilenyer.neocities.org/bsky/oauth/client-metadata.json\
            &request_uri={request_uri}"
            print("\nAuthorization URL:")
            print(auth_url)
            print("\nOpen this URL in a browser to complete the authorization process.")
            return True
        else:
            print("PAR request failed. Check the logs for details.")
            return False
    else:
        logging.warning("No PAR endpoint available, cannot proceed with OAuth flow")
        return False


if __name__ == "__main__":
    main()
