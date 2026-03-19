"""
LinkedIn API integration — OAuth 2.0 flow + post creation.
Uses Community Management API for personal profile posting.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx
from config import LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, LINKEDIN_REDIRECT_URI

logger = logging.getLogger(__name__)

OAUTH_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
OAUTH_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

# All scopes from added products
SCOPES = "openid profile w_member_social"


def get_auth_url(state: str = "random_state") -> str:
    """Generate LinkedIn OAuth authorization URL."""
    params = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "state": state,
        "scope": SCOPES,
    }
    return f"{OAUTH_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange authorization code for access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": LINKEDIN_REDIRECT_URI,
                "client_id": LINKEDIN_CLIENT_ID,
                "client_secret": LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()

        access_token = data["access_token"]
        expires_in = data.get("expires_in", 5184000)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        # Try to get person URN from token introspection
        person_urn = None

        # Try /v2/me
        for endpoint in ["https://api.linkedin.com/v2/me", "https://api.linkedin.com/v2/userinfo"]:
            try:
                r = await client.get(endpoint, headers={"Authorization": f"Bearer {access_token}"})
                logger.info(f"{endpoint} → {r.status_code}: {r.text[:200]}")
                if r.status_code == 200:
                    profile = r.json()
                    pid = profile.get("id") or profile.get("sub")
                    if pid:
                        person_urn = f"urn:li:person:{pid}"
                        break
            except Exception as e:
                logger.warning(f"{endpoint} failed: {e}")

        # Fallback to env
        if not person_urn:
            from config import LINKEDIN_PERSON_URN
            person_urn = LINKEDIN_PERSON_URN or "urn:li:person:unknown"
            logger.info(f"Using env LINKEDIN_PERSON_URN: {person_urn}")

        return {
            "access_token": access_token,
            "expires_at": expires_at,
            "person_urn": person_urn,
        }


async def upload_image_to_linkedin(client: httpx.AsyncClient, access_token: str, person_urn: str, image_url: str) -> str:
    """Download image from URL and upload to LinkedIn. Returns asset URN or empty string."""
    try:
        # Step 1: Download image
        img_resp = await client.get(image_url, timeout=15)
        img_resp.raise_for_status()
        image_bytes = img_resp.content

        author = person_urn.replace("urn:li:member:", "urn:li:person:")

        # Step 2: Register upload
        register_payload = {
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": author,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent"
                    }
                ]
            }
        }

        reg_resp = await client.post(
            "https://api.linkedin.com/v2/assets?action=registerUpload",
            json=register_payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        
        if reg_resp.status_code != 200:
            logger.error(f"Image register failed: {reg_resp.status_code} {reg_resp.text}")
            return ""

        reg_data = reg_resp.json()
        upload_url = reg_data["value"]["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
        asset = reg_data["value"]["asset"]

        # Step 3: Upload binary
        upload_resp = await client.put(
            upload_url,
            content=image_bytes,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "image/png",
            },
            timeout=30,
        )

        if upload_resp.status_code in (200, 201):
            logger.info(f"Image uploaded to LinkedIn: {asset}")
            return asset
        else:
            logger.error(f"Image upload failed: {upload_resp.status_code}")
            return ""

    except Exception as e:
        logger.error(f"Image upload error: {e}")
        return ""


async def post_to_linkedin(access_token: str, person_urn: str, text: str, image_url: str = None) -> dict:
    """Create a text or image post on LinkedIn. Tries multiple API endpoints."""
    
    author_person = person_urn.replace("urn:li:member:", "urn:li:person:")
    author_member = person_urn.replace("urn:li:person:", "urn:li:member:")
    
    logger.info(f"Attempting LinkedIn post. Input URN: {person_urn}")
    logger.info(f"  author_person: {author_person}")
    logger.info(f"  author_member: {author_member}")
    if image_url:
        logger.info(f"  image_url: {image_url}")

    async with httpx.AsyncClient() as client:
        # Upload image if provided
        image_asset = ""
        if image_url:
            image_asset = await upload_image_to_linkedin(client, access_token, author_person, image_url)
            logger.info(f"  image_asset: {image_asset or 'FAILED'}")

        # Method 1: /v2/shares
        shares_payload = {
            "owner": author_person,
            "text": {"text": text},
            "distribution": {"linkedInDistributionTarget": {}}
        }
        if image_asset:
            shares_payload["content"] = {
                "contentEntities": [{
                    "entity": image_asset,
                }],
                "shareMediaCategory": "IMAGE"
            }

        resp = await client.post(
            "https://api.linkedin.com/v2/shares",
            json=shares_payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        if resp.status_code == 201:
            data = resp.json()
            post_id = data.get("id", resp.headers.get("x-restli-id", "unknown"))
            logger.info(f"Posted to LinkedIn via /v2/shares: {post_id}")
            return {"success": True, "post_id": str(post_id)}

        logger.warning(f"/v2/shares failed ({resp.status_code}): {resp.text}")

        # Method 2: /v2/ugcPosts with urn:li:person
        ugc_payload = {
            "author": author_person,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "IMAGE" if image_asset else "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            }
        }
        if image_asset:
            ugc_payload["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [{
                "status": "READY",
                "media": image_asset,
            }]

        resp2 = await client.post(
            "https://api.linkedin.com/v2/ugcPosts",
            json=ugc_payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        if resp2.status_code == 201:
            post_id = resp2.headers.get("x-restli-id", "unknown")
            logger.info(f"Posted to LinkedIn via /v2/ugcPosts (person): {post_id}")
            return {"success": True, "post_id": post_id}

        logger.warning(f"/v2/ugcPosts person failed ({resp2.status_code}): {resp2.text}")

        # Method 3: /v2/ugcPosts with urn:li:member
        ugc_payload["author"] = author_member
        resp3 = await client.post(
            "https://api.linkedin.com/v2/ugcPosts",
            json=ugc_payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        if resp3.status_code == 201:
            post_id = resp3.headers.get("x-restli-id", "unknown")
            logger.info(f"Posted to LinkedIn via /v2/ugcPosts (member): {post_id}")
            return {"success": True, "post_id": post_id}

        logger.error(f"All LinkedIn post methods failed. Last: {resp3.status_code} {resp3.text}")
        return {"success": False, "error": f"All methods failed. Last error: {resp3.text}"}


async def check_token_valid(access_token: str) -> bool:
    """Check if LinkedIn token is still valid."""
    try:
        async with httpx.AsyncClient() as client:
            # Try /v2/me first, then /v2/userinfo
            for url in ["https://api.linkedin.com/v2/me", "https://api.linkedin.com/v2/userinfo"]:
                resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
                if resp.status_code == 200:
                    return True
            return False
    except Exception:
        return False
