"""
Microsoft account authentication (OAuth 2.0 device-code flow) for PyVortex.

Gives a Minecraft (premium) access token + UUID + username that can be passed
to the game so it authenticates properly on real servers.

Flow (all standard, no external dependencies):
  1. Request a device code from Microsoft OAuth v2.0.
  2. Ask the user to open the verification URL and enter the code.
  3. Poll until the user approves -> Microsoft OAuth access token.
  4. Exchange it for an Xbox Live token (XBL).
  5. Exchange for an XSTS token.
  6. Login with Xbox -> Minecraft access token + username + profile UUID.
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error

# Standard launcher User-Agent. Some Microsoft/Minecraft endpoints reject
# requests that omit it.
USER_AGENT = "PyVortex/1.0 (Minecraft launcher)"

# A public Microsoft app client id for the device-code (consumers) OAuth flow.
# The old Minecraft 'LiveAcc' id "00000000402b5328" is no longer registered in the
# consumers tenant and returns AADSTS700016 (HTTP 400). We instead use the client
# id of Prism Launcher's registered, in-production Entra app, which works for
# device-code auth and is on the api.minecraftservices.com allowlist.
# (Fallback that also returns 200 on the devicecode endpoint, if ever needed:
#  LiquidLauncher's "0add8caf-2cc6-4546-b798-c3d171217dd9".)
MS_CLIENT_ID = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb"

OAUTH_DEVICE = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
OAUTH_TOKEN = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBL_AUTH = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_AUTH = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE = "https://api.minecraftservices.com/minecraft/profile"


class AuthError(Exception):
    """Raised on any step of the (premium) login flow."""


def _post(url, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _form_post(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": USER_AGENT},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
#  Device code flow
# --------------------------------------------------------------------------- #
class DeviceCodeSession:
    """Coordinates an interactive device-code login with the user."""

    def __init__(self, poll_interval=5, timeout=600):
        self.device_code = None
        self.user_code = None
        self.verification_uri = None
        self.expires_in = 0
        self.interval = poll_interval
        self.timeout = timeout
        self._requested = False
        self.access_token = None  # raw MS OAuth access token (not Minecraft)

    def start(self):
        """Request a device code from Microsoft and return (uri, user_code)."""
        resp = _form_post(OAUTH_DEVICE, {
            "client_id": MS_CLIENT_ID,
            "scope": "XboxLive.signin offline_access",
        })
        self.device_code = resp["device_code"]
        self.user_code = resp["user_code"]
        self.verification_uri = resp["verification_uri"]
        self.expires_in = resp["expires_in"]
        self.interval = resp.get("interval", self.interval)
        self._requested = True
        return self.verification_uri, self.user_code

    def poll(self):
        """Poll for the MS OAuth access token. Returns it, or raises AuthError
        with a friendly message (user pending / expired / denied)."""
        elapsed = 0
        while elapsed < min(self.expires_in or self.timeout, self.timeout):
            try:
                resp = _form_post(OAUTH_TOKEN, {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": MS_CLIENT_ID,
                    "device_code": self.device_code,
                })
                self.access_token = resp["access_token"]
                return self.access_token
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8")
                try:
                    err = json.loads(body)
                    code = err.get("error", "")
                except Exception:
                    code = ""
                if code == "authorization_pending":
                    elapsed += self.interval
                    time.sleep(self.interval)
                    continue
                if code == "slow_down":
                    self.interval += 5
                    elapsed += self.interval
                    time.sleep(self.interval)
                    continue
                if code == "authorization_declined":
                    raise AuthError("Login declined by user.")
                if code in ("expired_token", "bad_verification_code"):
                    raise AuthError("The login code expired. Please try again.")
                raise AuthError(f"Device-code error: {code or e}")
            except urllib.error.URLError:
                elapsed += self.interval
                time.sleep(self.interval)
                continue
        raise AuthError("Login timed out. Please try again.")


# --------------------------------------------------------------------------- #
#  Token / profile exchange
# --------------------------------------------------------------------------- #
def xbox_live_token(ms_access_token):
    """Exchange the Microsoft OAuth token for an Xbox Live (XBL) token."""
    resp = _post(XBL_AUTH, {
        "Properties": {
            "AuthMethod": "RPS",
            "SiteName": "user.auth.xboxlive.com",
            "RpsTicket": "d=" + ms_access_token,
        },
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    })
    return resp["Token"], resp["DisplayClaims"]["xui"][0]["uhs"]


def xsts_token(xbl_token):
    """Exchange the XBL token for an XSTS token."""
    resp = _post(XSTS_AUTH, {
        "Properties": {
            "SandboxId": "RETAIL",
            "UserTokens": [xbl_token],
        },
        "RelyingParty": "rp://api.minecraftservices.com/",
        "TokenType": "JWT",
    })
    return resp["Token"]


def minecraft_login(xbl_token, uhs):
    """Exchange the XSTS token for a Minecraft access token."""
    try:
        resp = _post(MC_AUTH, {"identityToken": f"XBL3.0 x={uhs};{xbl_token}"})
    except urllib.error.HTTPError as e:
        msg = _http_error_message(e)
        raise AuthError(
            "Failed to log in with Xbox token. "
            + (f"The server returned: {msg}. " if msg else "")
            + "Make sure the account isn't child-managed, the app registration is "
            "valid, and the token is fresh.") from e
    return resp["access_token"], resp.get("username")


def minecraft_profile(access_token):
    """Fetch the Minecraft profile (UUID + name) for a valid access token."""
    req = urllib.request.Request(
        MC_PROFILE,
        headers={"Authorization": f"Bearer {access_token}",
                 "Accept": "application/json",
                 "User-Agent": USER_AGENT},
        method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise AuthError(
                "This Microsoft account does not own Minecraft: Java Edition. "
                "Login failed: the account needs a Minecraft: Java Edition "
                "license (Bedrock-only or child-managed accounts are not "
                "supported).") from e
        msg = _http_error_message(e)
        raise AuthError(
            "Could not fetch the Minecraft profile "
            + (f"({msg}). " if msg else ". ")
            + "Please try again.") from e


def _http_error_message(e):
    """Extract a readable error message from an HTTPError, or '' if none."""
    try:
        body = e.read().decode("utf-8")
    except Exception:
        return ""
    try:
        data = json.loads(body)
        return data.get("errorMessage") or data.get("error") or body
    except Exception:
        return body


def complete_login(session, display=None):
    """Finish a login from an already-started DeviceCodeSession.

    Polls for approval, then exchanges the Microsoft token through XBL -> XSTS ->
    Minecraft, and fetches the profile. `display(message)` (optional) is called
    with status for the user. Returns a dict: {type, name, uuid, access_token}.
    """
    if display:
        display("Waiting for approval...")
    ms_access_token = session.poll()
    if display:
        display("Authenticating Xbox Live...")
    xbl, uhs = xbox_live_token(ms_access_token)
    xsts = xsts_token(xbl)
    mc_token, mc_username = minecraft_login(xsts, uhs)
    profile = minecraft_profile(mc_token)
    return {
        "type": "microsoft",
        "name": profile.get("name") or mc_username or "Player",
        "uuid": profile.get("id"),
        "access_token": mc_token,
    }


def login_microsoft(display=None, create_session=None):
    """Run the full interactive login.

    `display(message)` (optional) is called with prompts/status for the user.
    `create_session()` (optional) returns a fresh DeviceCodeSession.
    Returns a dict: {type, name, uuid, access_token} on success.
    """
    if create_session is None:
        session = DeviceCodeSession()
    else:
        session = create_session()

    if display:
        display("Requesting Microsoft device code...")
    uri, user_code = session.start()
    if display:
        display(f"Open: {uri}\nEnter code: {user_code}")

    return complete_login(session, display)
