# server_sdk.py
"""
Facebook Ads MCP — SDK Extension Layer
Registers 57 additional tools on the same FastMCP instance from server.py,
using the official facebook-python-business-sdk (v25) for all new operations.

Tool groups (sections 1–14):
  1  Copy operations (campaign/adset/ad)
  2  Audiences (custom, lookalike, reach estimate)
  3  Insights with breakdowns
  4  Creative assets (image/video upload, previews)
  5  Product catalog & commerce
  6  Targeting search
  7  Advantage+ / Meta AI automation
  8  Page tools
  9  Write tools (campaign/adset/ad/creative CRUD)
  10 Pixels & custom conversions
  11 Budget schedules (dayparting)
  12 Saved audiences
  13 Product feeds
  14 Split tests (A/B)

Entry point: update manifest.json → server.entry_point = "server_sdk.py"
"""

import json
import mimetypes
import os
import re
import tempfile
import time
import requests as _req
from typing import Any, Dict, List, Optional

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.adimage import AdImage
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.adpreview import AdPreview
from facebook_business.adobjects.customaudience import CustomAudience
from facebook_business.adobjects.productcatalog import ProductCatalog
from facebook_business.adobjects.productset import ProductSet
from facebook_business.adobjects.productitem import ProductItem
from facebook_business.adobjects.targetingsearch import TargetingSearch
from facebook_business.adobjects.business import Business
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.adspixel import AdsPixel
from facebook_business.adobjects.customconversion import CustomConversion
from facebook_business.adobjects.savedaudience import SavedAudience
from facebook_business.adobjects.productfeed import ProductFeed

# Re-use the existing FastMCP instance — imports + registers all 28 original tools
from server import mcp, _get_fb_access_token

FB_API_VERSION = "v22.0"
FB_GRAPH_URL = f"https://graph.facebook.com/{FB_API_VERSION}"

# ── SDK Bootstrap ──────────────────────────────────────────────────────────────

def _init_sdk() -> str:
    """Init SDK with cached access token. Returns the token string."""
    token = _get_fb_access_token()
    FacebookAdsApi.init(access_token=token)
    return token


def _to_dict(obj: Any) -> Any:
    """Recursively convert an SDK object / collection to plain JSON-serialisable types."""
    if obj is None:
        return None
    if hasattr(obj, "export_all_data"):
        raw = obj.export_all_data()
        return {k: _to_dict(v) for k, v in raw.items()} if isinstance(raw, dict) else raw
    if isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


def _edge(edge_iter, max_items: int = 500) -> List[Dict]:
    """Drain an SDK EdgeIterator up to max_items and return plain dicts."""
    results = []
    for item in edge_iter:
        results.append(_to_dict(item))
        if len(results) >= max_items:
            break
    return results


def _post(path: str, payload: Dict) -> Dict:
    """Thin POST wrapper using requests (mirrors _make_graph_api_post in server.py)."""
    token = _get_fb_access_token()
    payload["access_token"] = token
    resp = _req.post(f"{FB_GRAPH_URL}/{path}", data=payload)
    resp.raise_for_status()
    return resp.json()


def _get(path: str, params: Optional[Dict] = None) -> Dict:
    """Thin GET wrapper using requests."""
    token = _get_fb_access_token()
    p = dict(params or {})
    p["access_token"] = token
    resp = _req.get(f"{FB_GRAPH_URL}/{path}", params=p)
    resp.raise_for_status()
    return resp.json()


def _delete(path: str) -> Dict:
    """Thin DELETE wrapper using requests."""
    token = _get_fb_access_token()
    resp = _req.delete(f"{FB_GRAPH_URL}/{path}", params={"access_token": token})
    resp.raise_for_status()
    return resp.json()


def _resolve_source(source: str):
    """Resolve a local path or URL to a local file path.

    Returns (local_path, is_temp). Caller must delete file if is_temp=True.
    Supported sources:
      - Local file path          → returned as-is
      - Google Drive share URL   → converted to direct download, saved to temp
      - Any public HTTP(S) URL   → downloaded to temp file
    """
    if not source.startswith("http://") and not source.startswith("https://"):
        return source, False

    download_url = source
    # Convert Google Drive share URL to direct download URL
    gdrive_match = re.search(r"drive\.google\.com/(?:file/d/|open\?id=)([^/?&]+)", source)
    if gdrive_match:
        file_id = gdrive_match.group(1)
        download_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t"

    session = _req.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    resp = session.get(download_url, stream=True, timeout=120)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "").split(";")[0].strip()

    # GDrive large-file: server returns HTML confirmation page instead of the file.
    # Parse the confirm token from the response and retry.
    if gdrive_match and "text/html" in content_type:
        # Try to find confirm token in either:
        #   <a href="/uc?export=download&id=...&confirm=TOKEN">
        #   or form action URL
        html_text = resp.text
        # Pattern 1: explicit confirm= query param in a href/form action
        token_match = re.search(r"confirm=([0-9A-Za-z_\-]+)", html_text)
        if token_match:
            confirm_token = token_match.group(1)
            file_id = gdrive_match.group(1)
            download_url = (
                f"https://drive.google.com/uc?export=download"
                f"&id={file_id}&confirm={confirm_token}"
            )
            resp = session.get(download_url, stream=True, timeout=300)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
        else:
            # Fallback: try the newer /uc?id=...&export=download with cookies set
            file_id = gdrive_match.group(1)
            download_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
            resp = session.get(download_url, stream=True, timeout=300)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip()

    ext = mimetypes.guess_extension(content_type) or ""
    # mimetypes sometimes returns .jpe for jpeg — normalise
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name, True


# ─────────────────────────────────────────────────────────────────────────────
# 1. COPY OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def copy_campaign(
    campaign_id: str,
    act_id: str,
    deep_copy: Optional[bool] = False,
    status_override: Optional[str] = "PAUSED",
) -> Dict:
    """Duplicate a campaign into an ad account.

    Args:
        campaign_id: Source campaign ID.
        act_id: Destination ad account ID prefixed with 'act_'.
        deep_copy: If True, also duplicates all child ad sets and ads. Default False.
        status_override: Status of the copy. Values: ACTIVE, PAUSED. Default PAUSED.

    Returns:
        Dict with 'id' of the new campaign and copy metadata.
    """
    _init_sdk()
    campaign = Campaign(fbid=campaign_id)
    result = campaign.create_copy(
        params={
            "account_id": act_id,
            "deep_copy": deep_copy,
            "status_override": status_override,
        }
    )
    return _to_dict(result)


@mcp.tool()
def copy_adset(
    adset_id: str,
    campaign_id: str,
    status_override: Optional[str] = "PAUSED",
) -> Dict:
    """Duplicate an ad set into a campaign.

    Args:
        adset_id: Source ad set ID.
        campaign_id: Destination campaign ID.
        status_override: Status of the copy. Default PAUSED.

    Returns:
        Dict with 'id' of the new ad set.
    """
    _init_sdk()
    adset = AdSet(fbid=adset_id)
    result = adset.create_copy(
        params={
            "campaign_id": campaign_id,
            "status_override": status_override,
        }
    )
    return _to_dict(result)


@mcp.tool()
def copy_ad(
    ad_id: str,
    adset_id: str,
    status_override: Optional[str] = "PAUSED",
) -> Dict:
    """Duplicate an ad into an ad set.

    Args:
        ad_id: Source ad ID.
        adset_id: Destination ad set ID.
        status_override: Status of the copy. Default PAUSED.

    Returns:
        Dict with 'id' of the new ad.
    """
    _init_sdk()
    ad = Ad(fbid=ad_id)
    result = ad.create_copy(
        params={
            "adset_id": adset_id,
            "status_override": status_override,
        }
    )
    return _to_dict(result)


# ─────────────────────────────────────────────────────────────────────────────
# 2. AUDIENCES
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_audiences(
    act_id: str,
    fields: Optional[List[str]] = None,
    limit: Optional[int] = 50,
) -> Dict:
    """List all custom audiences in an ad account.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        fields: Fields to return. Default: id, name, subtype, approximate_count_lower_bound,
            description, delivery_status, time_created, time_updated.
        limit: Max results. Default 50.

    Returns:
        Dict with 'data' list of audiences.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    default_fields = [
        "id", "name", "subtype",
        "approximate_count_lower_bound",
        "approximate_count_upper_bound",
        "description", "delivery_status",
        "time_created", "time_updated",
        "data_source",
    ]
    audiences = account.get_custom_audiences(
        fields=fields or default_fields,
        params={"limit": limit},
    )
    return {"data": _edge(audiences, max_items=limit)}


@mcp.tool()
def create_custom_audience(
    act_id: str,
    name: str,
    subtype: str,
    description: Optional[str] = None,
    retention_days: Optional[int] = 30,
    rule: Optional[Dict] = None,
    prefill: Optional[bool] = None,
) -> Dict:
    """Create a custom audience.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Audience name.
        subtype: Audience type. Values:
            CUSTOM       — customer list (upload emails/phones via upload_custom_audience_users)
            WEBSITE      — website visitors via Pixel
            APP          — app activity audience
            ENGAGEMENT   — people who engaged with your Facebook/Instagram content
        description: Optional description.
        retention_days: Days to keep users in audience (1–180). Default 30.
        rule: Rule spec for WEBSITE/APP/ENGAGEMENT types. Example for WEBSITE:
            {"inclusions": {"operator": "or", "rules": [
              {"event_sources": [{"id": "<pixel_id>", "type": "pixel"}],
               "retention_seconds": 2592000,
               "filter": {"operator": "and", "filters": [
                 {"field": "url", "operator": "i_contains", "value": "chotot.com"}
               ]}}
            ]}}
        prefill: For WEBSITE audiences — include historical data. Default None (use API default).

    Returns:
        Dict with 'id' of the created audience.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    params: Dict[str, Any] = {
        "name": name,
        "subtype": subtype,
    }
    if description:
        params["description"] = description
    if retention_days is not None:
        params["retention_days"] = retention_days
    if rule is not None:
        params["rule"] = json.dumps(rule)
    if prefill is not None:
        params["prefill"] = prefill

    audience = account.create_custom_audience(params=params)
    return _to_dict(audience)


@mcp.tool()
def upload_custom_audience_users(
    audience_id: str,
    emails: Optional[List[str]] = None,
    phones: Optional[List[str]] = None,
    is_raw: Optional[bool] = True,
) -> Dict:
    """Add users to a CUSTOM subtype audience by uploading email/phone list.

    Values are SHA-256 hashed before upload when is_raw=True (default).

    Args:
        audience_id: Target audience ID (must be subtype=CUSTOM).
        emails: List of email addresses to add.
        phones: List of phone numbers (E.164 format preferred, e.g. '+84901234567').
        is_raw: Hash values before upload when True (default).
            Set False only if you have already hashed them.

    Returns:
        Dict with 'num_received', 'num_invalid_entries', 'invalid_entry_samples'.
    """
    import hashlib

    _init_sdk()

    def sha256(val: str) -> str:
        return hashlib.sha256(val.strip().lower().encode()).hexdigest()

    if not emails and not phones:
        return {"error": "Provide at least one of: emails, phones"}

    if emails and phones:
        schema = ["EMAIL", "PHONE"]
        data = [
            [sha256(e) if is_raw else e, sha256(p) if is_raw else p]
            for e, p in zip(emails, phones)
        ]
    elif emails:
        schema = ["EMAIL"]
        data = [[sha256(e) if is_raw else e] for e in emails]
    else:
        schema = ["PHONE"]
        data = [[sha256(p) if is_raw else p] for p in phones]

    audience = CustomAudience(fbid=audience_id)
    result = audience.create_users_replace(
        params={
            "schema": schema,
            "data": data,
            "is_raw": False,  # already hashed above
        }
    )
    return _to_dict(result)


@mcp.tool()
def create_lookalike_audience(
    act_id: str,
    name: str,
    origin_audience_id: str,
    country: str,
    ratio: Optional[float] = 0.01,
    lookalike_type: Optional[str] = "similarity",
) -> Dict:
    """Create a Lookalike Audience based on a source custom audience.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Name for the new lookalike audience.
        origin_audience_id: Source custom audience ID to model from.
        country: Two-letter country code, e.g. 'VN', 'SG', 'US'.
        ratio: Size ratio 0.01–0.20. 0.01 = top 1% most similar. Default 0.01.
        lookalike_type: 'similarity' (smaller, more precise) or 'reach' (larger). Default 'similarity'.

    Returns:
        Dict with 'id' of the created lookalike audience.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    params = {
        "name": name,
        "subtype": "LOOKALIKE",
        "lookalike_spec": json.dumps({
            "origin_ids": origin_audience_id,
            "country": country,
            "ratio": ratio,
            "type": lookalike_type,
        }),
    }
    audience = account.create_custom_audience(params=params)
    return _to_dict(audience)


@mcp.tool()
def get_reach_estimate(
    act_id: str,
    targeting: Dict,
    optimization_goal: Optional[str] = "IMPRESSIONS",
) -> Dict:
    """Estimate potential reach for a targeting spec before creating an ad set.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        targeting: Targeting spec dict. Example:
            {"geo_locations": {"countries": ["VN"]}, "age_min": 18, "age_max": 65}
        optimization_goal: Optimization goal for context. Default 'IMPRESSIONS'.

    Returns:
        Dict with 'users_lower_bound' and 'users_upper_bound' estimated reach.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    result = account.get_reach_estimate(
        params={
            "targeting_spec": json.dumps(targeting),
            "optimization_goal": optimization_goal,
        }
    )
    data = list(result)
    return _to_dict(data[0]) if data else {"users_lower_bound": 0, "users_upper_bound": 0}


@mcp.tool()
def delete_audience(audience_id: str) -> Dict:
    """Delete a custom audience permanently.

    Args:
        audience_id: The custom audience ID to delete.

    Returns:
        Dict with 'success' boolean.
    """
    _init_sdk()
    audience = CustomAudience(fbid=audience_id)
    audience.api_delete()
    return {"success": True, "deleted_id": audience_id}


# ─────────────────────────────────────────────────────────────────────────────
# 3. INSIGHTS WITH BREAKDOWNS
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_insights_with_breakdown(
    object_id: str,
    level: str,
    breakdowns: List[str],
    fields: Optional[List[str]] = None,
    date_preset: Optional[str] = "last_30d",
    time_range: Optional[Dict] = None,
    limit: Optional[int] = 200,
) -> Dict:
    """Get ad insights broken down by demographic or placement dimensions.

    Args:
        object_id: ID of the object to analyse. Use 'act_xxx' for account-level,
            or a campaign/adset/ad ID for lower levels.
        level: Aggregation level. Values: account, campaign, adset, ad.
        breakdowns: Breakdown dimensions. Common values:
            age                 — age groups (18-24, 25-34, ...)
            gender              — male / female / unknown
            country             — country of user
            region              — region/province
            device_platform     — mobile / desktop
            publisher_platform  — facebook / instagram / audience_network / messenger
            platform_position   — feed / story / reel / search / ...
            impression_device   — iphone / android_smartphone / desktop / ...
            product_id          — (for catalog/dynamic ads)
            Example: ["age", "gender"] or ["publisher_platform", "platform_position"]
        fields: Metrics to return. Default: impressions, clicks, spend, cpm, cpc, ctr, reach.
        date_preset: Relative date range. Default 'last_30d'.
        time_range: Explicit range {'since': 'YYYY-MM-DD', 'until': 'YYYY-MM-DD'}.
            Overrides date_preset when provided.
        limit: Max rows to return. Default 200.

    Returns:
        Dict with 'data' list — each row includes breakdown columns + metric columns.
    """
    _init_sdk()

    default_fields = ["impressions", "clicks", "spend", "cpm", "cpc", "ctr", "reach"]

    level_map = {"account": AdAccount, "campaign": Campaign, "adset": AdSet, "ad": Ad}

    if object_id.startswith("act_"):
        obj = AdAccount(fbid=object_id)
    else:
        cls = level_map.get(level)
        if not cls:
            return {"error": f"Unknown level '{level}'. Use: account, campaign, adset, ad"}
        obj = cls(fbid=object_id)

    params: Dict[str, Any] = {
        "level": level,
        "breakdowns": breakdowns,
        "limit": limit,
    }
    if time_range:
        params["time_range"] = json.dumps(time_range)
    else:
        params["date_preset"] = date_preset

    insights = obj.get_insights(fields=fields or default_fields, params=params)
    return {"data": _edge(insights, max_items=limit)}


# ─────────────────────────────────────────────────────────────────────────────
# 4. CREATIVE ASSETS
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def upload_ad_image(
    act_id: str,
    image_path: str,
    name: Optional[str] = None,
) -> Dict:
    """Upload an image to an ad account for use in creatives.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        image_path: Local file path OR a public URL (HTTP/HTTPS) OR a
            Google Drive share URL (drive.google.com/file/d/...).
            Supports JPEG, PNG, GIF.
        name: Display name for the image. Defaults to the filename.

    Returns:
        Dict with 'hash' (use as image_hash in ad creatives), 'url',
        'name', 'width', 'height'.
    """
    token = _init_sdk()
    local_path, is_temp = _resolve_source(image_path)
    try:
        if not os.path.isfile(local_path):
            return {"error": f"File not found: {local_path}"}
        file_basename = os.path.basename(local_path)
        img_name = name or file_basename
        with open(local_path, "rb") as f:
            resp = _req.post(
                f"{FB_GRAPH_URL}/{act_id}/adimages",
                data={"access_token": token, "name": img_name},
                files={"filename": (file_basename, f)},
            )
        resp.raise_for_status()
        data = resp.json()
        images = data.get("images", {})
        if images:
            return next(iter(images.values()))
        return data
    finally:
        if is_temp and os.path.exists(local_path):
            os.unlink(local_path)


@mcp.tool()
def upload_ad_video(
    act_id: str,
    video_path: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    poll_interval_seconds: Optional[int] = 5,
    max_wait_seconds: Optional[int] = 300,
) -> Dict:
    """Upload a video file to an ad account. Blocks until encoding is complete (or timeout).

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        video_path: Absolute local path to the video, e.g.
            '/Users/yourname/Desktop/ad_video.mp4'.
        title: Optional video title.
        description: Optional video description.
        poll_interval_seconds: Seconds between encoding status checks. Default 5.
        max_wait_seconds: Max wait time before returning with 'status: timeout'. Default 300.

    Returns:
        Dict with 'id' (video_id), 'status' (ready/error/timeout), and metadata.
        Use 'id' as video_id in ad creative's video_data spec.
    """
    token = _init_sdk()
    local_path, is_temp = _resolve_source(video_path)
    try:
        if not os.path.isfile(local_path):
            return {"error": f"File not found: {local_path}"}

        data: Dict[str, Any] = {"access_token": token}
        if title:
            data["title"] = title
        if description:
            data["description"] = description

        video_name = os.path.basename(local_path)
        with open(local_path, "rb") as f:
            resp = _req.post(
                f"{FB_GRAPH_URL}/{act_id}/advideos",
                data=data,
                files={"source": (video_name, f, "video/mp4")},
            )
        resp.raise_for_status()
        raw = resp.json()
        video_id = raw.get("id")
        if not video_id:
            return {"error": "Upload failed — no video ID returned", "raw": raw}
    finally:
        if is_temp and os.path.exists(local_path):
            os.unlink(local_path)

    # Poll for encoding completion
    elapsed = 0
    while elapsed < max_wait_seconds:
        vid_obj = AdVideo(fbid=video_id)
        status_data = _to_dict(vid_obj.api_get(fields=["status", "title", "length"]))
        video_status = status_data.get("status", {})
        if isinstance(video_status, dict):
            video_status = video_status.get("video_status", "")
        if video_status in ("ready", "complete"):
            return {"id": video_id, "status": "ready", **status_data}
        if video_status == "error":
            return {"id": video_id, "status": "error", **status_data}
        time.sleep(poll_interval_seconds)
        elapsed += poll_interval_seconds

    return {
        "id": video_id,
        "status": "timeout",
        "message": f"Encoding not done after {max_wait_seconds}s. Use get_video_upload_status('{video_id}') to check later.",
    }


@mcp.tool()
def get_video_upload_status(video_id: str) -> Dict:
    """Check the encoding/processing status of an uploaded video.

    Args:
        video_id: Video ID returned by upload_ad_video.

    Returns:
        Dict with 'id', 'status' (processing/ready/error), 'title', 'length'.
    """
    _init_sdk()
    vid = AdVideo(fbid=video_id)
    return _to_dict(vid.api_get(fields=["status", "title", "description", "length", "thumbnails"]))


@mcp.tool()
def get_ad_previews(
    ad_id: str,
    ad_formats: Optional[List[str]] = None,
) -> Dict:
    """Get rendered HTML preview iframes for an ad across different placements.

    Args:
        ad_id: Ad ID to preview.
        ad_formats: Placement formats to generate previews for. Common values:
            DESKTOP_FEED_STANDARD       — Facebook desktop news feed
            MOBILE_FEED_STANDARD        — Facebook mobile news feed
            INSTAGRAM_STANDARD          — Instagram feed
            INSTAGRAM_STORY             — Instagram story
            FACEBOOK_STORY              — Facebook story
            MESSENGER_MOBILE_INBOX_MEDIA — Messenger inbox
            AUDIENCE_NETWORK_OUTSTREAM_VIDEO — Audience Network video
            Default: DESKTOP_FEED_STANDARD, MOBILE_FEED_STANDARD, INSTAGRAM_STANDARD.

    Returns:
        Dict with 'data' list — each item has 'ad_format' and 'body' (HTML iframe).
        Render 'body' in a browser to see the preview.
    """
    _init_sdk()
    default_formats = [
        "DESKTOP_FEED_STANDARD",
        "MOBILE_FEED_STANDARD",
        "INSTAGRAM_STANDARD",
    ]
    ad = Ad(fbid=ad_id)
    previews = []
    for fmt in (ad_formats or default_formats):
        for p in ad.get_previews(params={"ad_format": fmt}):
            d = _to_dict(p)
            d["ad_format"] = fmt
            previews.append(d)
    return {"data": previews}


# ─────────────────────────────────────────────────────────────────────────────
# 5. CATALOG & COMMERCE
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_product_catalogs(business_id: str) -> Dict:
    """List all product catalogs owned by a Business Manager.

    Args:
        business_id: Business Manager ID (numeric, no prefix).

    Returns:
        Dict with 'data' list of catalogs (id, name, vertical, product_count).
    """
    _init_sdk()
    biz = Business(fbid=business_id)
    catalogs = biz.get_owned_product_catalogs(
        fields=["id", "name", "vertical", "product_count"]
    )
    return {"data": _edge(catalogs)}


@mcp.tool()
def create_product_catalog(
    business_id: str,
    name: str,
    vertical: Optional[str] = "commerce",
) -> Dict:
    """Create a new product catalog in a Business Manager.

    Args:
        business_id: Business Manager ID.
        name: Catalog name.
        vertical: Catalog type. Values:
            commerce (default), vehicles, real_estate,
            flights, hotels, home_listings.

    Returns:
        Dict with 'id' of the new catalog.
    """
    _init_sdk()
    biz = Business(fbid=business_id)
    catalog = biz.create_owned_product_catalog(
        params={"name": name, "vertical": vertical}
    )
    return _to_dict(catalog)


@mcp.tool()
def get_product_sets(
    catalog_id: str,
    limit: Optional[int] = 50,
) -> Dict:
    """List product sets in a catalog.

    Args:
        catalog_id: Product catalog ID.
        limit: Max results. Default 50.

    Returns:
        Dict with 'data' list of product sets (id, name, filter, product_count).
    """
    _init_sdk()
    catalog = ProductCatalog(fbid=catalog_id)
    sets = catalog.get_product_sets(
        fields=["id", "name", "filter", "product_count"],
        params={"limit": limit},
    )
    return {"data": _edge(sets, max_items=limit)}


@mcp.tool()
def create_product_set(
    catalog_id: str,
    name: str,
    product_filter: Optional[Dict] = None,
) -> Dict:
    """Create a product set (filtered subset) within a catalog for use in Dynamic Ads.

    Args:
        catalog_id: Parent catalog ID.
        name: Product set name.
        product_filter: Filter dict to auto-include matching products. Example:
            {"availability": {"is_any": ["in stock"]}}
            {"retailer_id": {"is_any": ["SKU001", "SKU002"]}}
            Omit for an all-products set.

    Returns:
        Dict with 'id' of the new product set.
    """
    _init_sdk()
    catalog = ProductCatalog(fbid=catalog_id)
    params: Dict[str, Any] = {"name": name}
    if product_filter:
        params["filter"] = json.dumps(product_filter)
    result = catalog.create_product_set(params=params)
    return _to_dict(result)


@mcp.tool()
def get_products(
    catalog_id: str,
    filter_str: Optional[str] = None,
    limit: Optional[int] = 50,
) -> Dict:
    """List products in a catalog.

    Args:
        catalog_id: Product catalog ID.
        filter_str: RSQL filter string. Example: "availability=available"
        limit: Max products to return. Default 50.

    Returns:
        Dict with 'data' list of products (id, retailer_id, name, price,
        currency, availability, url, image_url).
    """
    _init_sdk()
    catalog = ProductCatalog(fbid=catalog_id)
    params: Dict[str, Any] = {"limit": limit}
    if filter_str:
        params["filter"] = filter_str
    products = catalog.get_products(
        fields=[
            "id", "retailer_id", "name", "price", "currency",
            "availability", "url", "image_url", "description",
        ],
        params=params,
    )
    return {"data": _edge(products, max_items=limit)}


@mcp.tool()
def create_product(
    catalog_id: str,
    retailer_id: str,
    name: str,
    price: int,
    currency: str,
    url: str,
    image_url: str,
    availability: Optional[str] = "in stock",
    description: Optional[str] = None,
    brand: Optional[str] = None,
    condition: Optional[str] = "new",
) -> Dict:
    """Add a single product to a catalog.

    Args:
        catalog_id: Target catalog ID.
        retailer_id: Your unique product SKU / internal ID.
        name: Product title.
        price: Price in minor units (e.g. cents). 50000 = $500.00 or 500,000 VND × 100.
            Facebook expects price as an integer in the currency's smallest unit.
        currency: ISO 4217 code, e.g. 'VND', 'SGD', 'USD'.
        url: Product page URL.
        image_url: Public URL of the product image (min 500×500px recommended).
        availability: 'in stock' (default), 'out of stock', 'preorder', 'available for order'.
        description: Optional product description.
        brand: Optional brand name.
        condition: 'new' (default), 'refurbished', 'used'.

    Returns:
        Dict with 'id' of the created product item.
    """
    _init_sdk()
    catalog = ProductCatalog(fbid=catalog_id)
    params: Dict[str, Any] = {
        "retailer_id": retailer_id,
        "name": name,
        "price": price,
        "currency": currency,
        "url": url,
        "image_url": image_url,
        "availability": availability,
        "condition": condition,
    }
    if description:
        params["description"] = description
    if brand:
        params["brand"] = brand
    result = catalog.create_product(params=params)
    return _to_dict(result)


@mcp.tool()
def update_product(
    product_id: str,
    name: Optional[str] = None,
    price: Optional[int] = None,
    availability: Optional[str] = None,
    description: Optional[str] = None,
    url: Optional[str] = None,
    image_url: Optional[str] = None,
) -> Dict:
    """Update fields on an existing product.

    Only provided fields are changed — omit to leave unchanged.

    Args:
        product_id: Product item ID.
        name: New product name.
        price: New price in minor currency units.
        availability: New availability status.
        description: New description.
        url: New product page URL.
        image_url: New image URL.

    Returns:
        Dict with 'success' boolean.
    """
    _init_sdk()
    product = ProductItem(fbid=product_id)
    params: Dict[str, Any] = {}
    if name is not None:
        params["name"] = name
    if price is not None:
        params["price"] = price
    if availability is not None:
        params["availability"] = availability
    if description is not None:
        params["description"] = description
    if url is not None:
        params["url"] = url
    if image_url is not None:
        params["image_url"] = image_url
    product.api_update(params=params)
    return {"success": True, "updated_id": product_id}


@mcp.tool()
def delete_product(product_id: str) -> Dict:
    """Delete a product from a catalog.

    Args:
        product_id: Product item ID to delete.

    Returns:
        Dict with 'success' boolean.
    """
    _init_sdk()
    product = ProductItem(fbid=product_id)
    product.api_delete()
    return {"success": True, "deleted_id": product_id}


@mcp.tool()
def batch_upload_products(
    catalog_id: str,
    requests: List[Dict],
) -> Dict:
    """Batch create, update, or delete multiple products in one API call (max 1000/batch).

    Args:
        catalog_id: Target product catalog ID.
        requests: List of operation objects. Each must have:
            - 'method': 'CREATE', 'UPDATE', or 'DELETE'
            - 'retailer_id': Your SKU (required for all methods)
            - Product fields for CREATE/UPDATE (name, price, currency, url, image_url, ...)
            Example:
            [
              {"method": "CREATE", "retailer_id": "SKU001", "name": "iPhone 15",
               "price": 2000000, "currency": "VND",
               "url": "https://chotot.com/p/1", "image_url": "https://img.chotot.com/1.jpg"},
              {"method": "UPDATE", "retailer_id": "SKU002", "price": 1500000},
              {"method": "DELETE", "retailer_id": "SKU003"}
            ]

    Returns:
        Dict with 'handles' list for tracking per-item batch status.
    """
    _init_sdk()
    catalog = ProductCatalog(fbid=catalog_id)
    result = catalog.create_batch(params={"requests": json.dumps(requests)})
    return _to_dict(result)


# ─────────────────────────────────────────────────────────────────────────────
# 6. TARGETING SEARCH
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_targeting_interests(
    query: str,
    limit: Optional[int] = 20,
) -> Dict:
    """Search for interest-based targeting options to use in ad set targeting.

    Args:
        query: Keyword, e.g. 'real estate', 'motorbike', 'online shopping', 'football'.
        limit: Max results. Default 20.

    Returns:
        Dict with 'data' list — each item has 'id', 'name', 'audience_size',
        'path' (category hierarchy), 'description'.
        Use 'id' in targeting spec: {"interests": [{"id": "...", "name": "..."}]}
    """
    _init_sdk()
    result = TargetingSearch.search(
        params={"q": query, "type": "adinterest", "limit": limit}
    )
    return {"data": _edge(result, max_items=limit)}


@mcp.tool()
def search_geo_locations(
    query: str,
    location_types: Optional[List[str]] = None,
    limit: Optional[int] = 20,
) -> Dict:
    """Search for geographic targeting locations by name.

    Args:
        query: Place name, e.g. 'Ho Chi Minh', 'Hanoi', 'Vietnam', 'Singapore'.
        location_types: Types to include. Values: country, region, city, zip,
            geo_market, electoral_district, country_group.
            Default: ['country', 'region', 'city'].
        limit: Max results. Default 20.

    Returns:
        Dict with 'data' list — each item has 'key', 'name', 'type',
        'country_code', 'region', 'region_id'.
        Use 'key' in targeting spec geo_locations object.
    """
    _init_sdk()
    result = TargetingSearch.search(
        params={
            "q": query,
            "type": "adgeolocation",
            "location_types": json.dumps(location_types or ["country", "region", "city"]),
            "limit": limit,
        }
    )
    return {"data": _edge(result, max_items=limit)}


@mcp.tool()
def search_targeting_behaviors(
    query: str,
    limit: Optional[int] = 20,
) -> Dict:
    """Search for behavior-based targeting options.

    Behaviors include purchase patterns, device usage, travel, digital activities, etc.

    Args:
        query: Keyword, e.g. 'online shopping', 'frequent traveler', 'iOS device'.
        limit: Max results. Default 20.

    Returns:
        Dict with 'data' list — each item has 'id', 'name', 'audience_size',
        'path', 'description'.
        Use 'id' in targeting spec: {"behaviors": [{"id": "...", "name": "..."}]}
    """
    _init_sdk()
    result = TargetingSearch.search(
        params={"q": query, "type": "behaviors", "limit": limit}
    )
    return {"data": _edge(result, max_items=limit)}


@mcp.tool()
def search_targeting_demographics(
    query: str,
    limit: Optional[int] = 20,
) -> Dict:
    """Search for demographic targeting options (education, life events, job titles, etc.).

    Args:
        query: Keyword, e.g. 'university student', 'software engineer', 'new parent'.
        limit: Max results. Default 20.

    Returns:
        Dict with 'data' list of demographic targeting options.
    """
    _init_sdk()
    result = TargetingSearch.search(
        params={
            "q": query,
            "type": "adTargetingCategory",
            "class": "demographics",
            "limit": limit,
        }
    )
    return {"data": _edge(result, max_items=limit)}


@mcp.tool()
def browse_targeting_categories(
    class_filter: Optional[str] = None,
) -> Dict:
    """Browse all available targeting categories without a search query.

    Useful for discovering what targeting options exist before searching.

    Args:
        class_filter: Narrow to a specific class. Values: interests, behaviors, demographics.
            Omit to return all classes.

    Returns:
        Dict with 'data' list of all categories grouped by type.
    """
    _init_sdk()
    params: Dict[str, Any] = {"type": "adTargetingCategory"}
    if class_filter:
        params["class"] = class_filter
    result = TargetingSearch.search(params=params)
    return {"data": _edge(result, max_items=500)}


# ─────────────────────────────────────────────────────────────────────────────
# 7. ADVANTAGE+ (META AI AUTOMATION)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def create_advantage_plus_shopping_campaign(
    act_id: str,
    name: str,
    daily_budget: int,
    country_code: str,
    pixel_id: str,
    status: Optional[str] = "PAUSED",
    start_time: Optional[str] = None,
    stop_time: Optional[str] = None,
    existing_customer_budget_percentage: Optional[int] = 30,
) -> Dict:
    """Create an Advantage+ Shopping Campaign (ASC) — Meta's fully AI-automated campaign.

    ASC lets Meta's AI control audience targeting, placement, and delivery.
    Recommended for e-commerce with conversion events already firing.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Campaign name.
        daily_budget: Daily budget in cents (e.g. 5000000 = $50,000.00 VND or $50 SGD).
        country_code: Target country code, e.g. 'VN', 'SG'.
        pixel_id: Meta Pixel ID for purchase conversion tracking.
        status: 'PAUSED' (default, save as draft) or 'ACTIVE'.
        start_time: ISO 8601 start time, e.g. '2026-05-01T00:00:00+0700'.
        stop_time: ISO 8601 end time.
        existing_customer_budget_percentage: % of budget for existing customers (0–100).
            Default 30 (Meta recommended).

    Returns:
        Dict with 'campaign_id', 'adset_id', 'status', and next-step instructions.
    """
    _init_sdk()

    # Step 1 — Create ASC campaign
    camp_payload: Dict[str, Any] = {
        "name": name,
        "objective": "OUTCOME_SALES",
        "status": status,
        "special_ad_categories": json.dumps([]),
        "smart_promotion_type": "AUTOMATED_SHOPPING_ADS",
    }
    if start_time:
        camp_payload["start_time"] = start_time
    if stop_time:
        camp_payload["stop_time"] = stop_time

    camp_result = _post(f"{act_id}/campaigns", camp_payload)
    campaign_id = camp_result.get("id")
    if not campaign_id:
        return {"error": "Campaign creation failed", "raw": camp_result}

    # Step 2 — Create ASC ad set with Advantage+ audience
    adset_payload: Dict[str, Any] = {
        "name": f"{name} — AdSet",
        "campaign_id": campaign_id,
        "daily_budget": daily_budget,
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "status": status,
        "targeting": json.dumps({"geo_locations": {"countries": [country_code]}}),
        "promoted_object": json.dumps({
            "pixel_id": pixel_id,
            "custom_event_type": "PURCHASE",
        }),
        "existing_customer_budget_percentage": existing_customer_budget_percentage,
    }

    adset_result = _post(f"{act_id}/adsets", adset_payload)
    adset_id = adset_result.get("id")

    return {
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "status": status,
        "next_steps": "Add creatives via create_ad_creative() then create_ad() pointing to this adset_id.",
    }


@mcp.tool()
def create_adset_with_advantage_audience(
    act_id: str,
    name: str,
    campaign_id: str,
    daily_budget: int,
    optimization_goal: str,
    billing_event: str,
    geo_countries: List[str],
    status: Optional[str] = "PAUSED",
    age_min: Optional[int] = 18,
    age_max: Optional[int] = 65,
    interest_ids: Optional[List[str]] = None,
) -> Dict:
    """Create an ad set with Advantage+ Audience enabled.

    With Advantage+ Audience, Meta's AI starts from your targeting suggestions
    but expands beyond them if it finds better-performing users.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Ad set name.
        campaign_id: Parent campaign ID.
        daily_budget: Daily budget in cents.
        optimization_goal: e.g. LINK_CLICKS, OFFSITE_CONVERSIONS, REACH, APP_INSTALLS.
        billing_event: IMPRESSIONS or LINK_CLICKS.
        geo_countries: Target country codes, e.g. ['VN'].
        status: 'PAUSED' (default) or 'ACTIVE'.
        age_min: Suggested minimum age (18–65). Default 18.
        age_max: Suggested maximum age (18–65). Default 65.
        interest_ids: Optional list of interest IDs as targeting suggestions.
            Get IDs via search_targeting_interests(). Meta may expand beyond these.

    Returns:
        Dict with 'id' of the created ad set.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)

    targeting: Dict[str, Any] = {
        "geo_locations": {"countries": geo_countries},
        "age_min": age_min,
        "age_max": age_max,
    }
    if interest_ids:
        targeting["interests"] = [{"id": iid} for iid in interest_ids]

    params: Dict[str, Any] = {
        "name": name,
        "campaign_id": campaign_id,
        "daily_budget": daily_budget,
        "optimization_goal": optimization_goal,
        "billing_event": billing_event,
        "targeting": json.dumps(targeting),
        "status": status,
        # Advantage+ Audience signal
        "targeting_automation": json.dumps({"advantage_audience": 1}),
    }

    adset = account.create_ad_set(params=params)
    return _to_dict(adset)


@mcp.tool()
def enable_advantage_creative(
    ad_id: str,
    enhancements: Optional[List[str]] = None,
) -> Dict:
    """Enable Advantage+ Creative enhancements on an existing ad.

    Meta's AI will automatically apply visual/text improvements to boost performance.

    Args:
        ad_id: Ad ID to update.
        enhancements: List of enhancements to enable. Common values:
            standard_enhancements       — all standard auto-enhancements
            image_templates             — auto-add text overlays
            image_touchups              — brightness/contrast adjustments
            image_uncrop                — auto-expand image to fit placement
            add_text_overlay            — add promotional text
            text_optimizations          — test multiple text versions
            Omit or pass [] to enable all standard enhancements (Meta default).

    Returns:
        Dict with 'success' boolean.
    """
    _init_sdk()

    # Advantage+ Creative is set via the `creative` field's `asset_feed_spec`
    # or via `degrees_of_freedom_spec` on the ad
    payload: Dict[str, Any] = {
        "degrees_of_freedom_spec": json.dumps({
            "creative_features_spec": {
                "standard_enhancements": {"enroll_status": "OPT_IN"}
            }
        })
    }
    result = _post(ad_id, payload)
    return {"success": True, "ad_id": ad_id, "raw": result}


@mcp.tool()
def get_performance_recommendations(act_id: str) -> Dict:
    """Get Meta's AI-generated performance recommendations for an ad account.

    Returns suggestions on budget changes, audience expansion, creative improvements,
    and other automated optimizations identified by Meta's system.

    Args:
        act_id: Ad account ID prefixed with 'act_'.

    Returns:
        Dict with 'data' list of recommendations, each with
        'title', 'message', 'recommendation_type', 'confidence', 'importance'.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    try:
        recs = account.get_ad_recommendations(
            fields=["title", "message", "recommendation_type", "confidence", "importance"]
        )
        return {"data": _edge(recs)}
    except Exception as e:
        return {"data": [], "note": f"No recommendations available or endpoint not accessible: {str(e)}"}


# ─────────────────────────────────────────────────────────────────────────────
# 8. PAGE TOOLS — Page posts lookup (requires Page Access Token)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_page_posts(
    page_id: str,
    limit: int = 10,
    page_access_token: Optional[str] = None,
) -> Dict:
    """List recent published posts from a Facebook Page.

    Requires a Page Access Token — a User Access Token alone is NOT sufficient
    (Facebook Graph API restriction for New Pages Experience).

    Token resolution order:
    1. Use page_access_token param if provided
    2. Try /me/accounts to get token automatically (works if current user is page admin
       and token has pages_show_list + pages_read_engagement scope)
    3. Return actionable error with instructions if both fail

    Args:
        page_id: Facebook Page ID, e.g. '300829936671250'
        limit: Number of posts to return (default 10, max 100)
        page_access_token: Optional Page Access Token. Provide this when the
            auto-fetch fails (i.e. current user token doesn't manage this page).
            Get it from: Graph API Explorer -> select page -> Generate Page Access Token.
            Or: Business Suite -> Settings -> Advanced -> Page Access Tokens.

    Returns:
        Dict with 'data' list, each item has:
            - id: full post ID string
            - post_id: the part after '_' (numeric post ID)
            - object_story_id: ready-to-use format '{page_id}_{post_id}' for create_ad_creative
            - message: post text (first 100 chars)
            - created_time: ISO timestamp
    """
    token = _get_fb_access_token()

    pat = page_access_token
    if not pat:
        r = _req.get(
            f"{FB_GRAPH_URL}/me/accounts",
            params={"fields": "id,access_token", "access_token": token, "limit": 100}
        )
        if r.status_code == 200:
            for acc in r.json().get("data", []):
                if acc.get("id") == page_id:
                    pat = acc.get("access_token")
                    break

    if not pat:
        return {
            "error": "page_access_token_required",
            "message": (
                f"Cannot fetch posts for page {page_id} with the current user token. "
                "Provide page_access_token param. "
                "Get it from: Graph API Explorer -> select page -> Generate Page Access Token. "
                "Or: Business Suite -> Settings -> Advanced -> Page Access Tokens."
            ),
        }

    r = _req.get(
        f"{FB_GRAPH_URL}/{page_id}/published_posts",
        params={"fields": "id,message,created_time", "limit": min(limit, 100), "access_token": pat},
    )
    if r.status_code != 200:
        return {"error": r.status_code, "detail": r.json()}

    posts = []
    for p in r.json().get("data", []):
        post_id_part = p["id"].split("_", 1)[-1]
        posts.append({
            "id": p["id"],
            "post_id": post_id_part,
            "object_story_id": f"{page_id}_{post_id_part}",
            "message": p.get("message", "")[:100],
            "created_time": p.get("created_time", ""),
        })
    return {"data": posts}


# ─────────────────────────────────────────────────────────────────────────────
# 9. WRITE TOOLS — Campaign / AdSet / Ad / Creative  (SDK-powered, full params)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def create_campaign(
    act_id: str,
    name: str,
    objective: str,
    status: Optional[str] = "PAUSED",
    special_ad_categories: Optional[List[str]] = None,
    daily_budget: Optional[int] = None,
    lifetime_budget: Optional[int] = None,
    spend_cap: Optional[int] = None,
    start_time: Optional[str] = None,
    stop_time: Optional[str] = None,
    bid_strategy: Optional[str] = None,
    buying_type: Optional[str] = None,
    smart_promotion_type: Optional[str] = None,
) -> Dict:
    """Create a new Facebook ad campaign. Use status='PAUSED' to save as draft.

    Args:
        act_id: Ad account ID prefixed with 'act_', e.g. 'act_1234567890'.
        name: Campaign name.
        objective: Campaign objective. Valid values:
            OUTCOME_TRAFFIC, OUTCOME_AWARENESS, OUTCOME_ENGAGEMENT,
            OUTCOME_LEADS, OUTCOME_SALES, OUTCOME_APP_PROMOTION.
        status: 'PAUSED' (default — save as draft) or 'ACTIVE' to launch immediately.
        special_ad_categories: Required field. Pass [] if none apply.
            Non-empty values: EMPLOYMENT, HOUSING, CREDIT, ISSUES_ELECTIONS_POLITICS.
        daily_budget: Daily budget in cents (e.g. 100000 = $10 SGD). For CBO campaigns.
            Mutually exclusive with lifetime_budget.
        lifetime_budget: Lifetime budget in cents. Mutually exclusive with daily_budget.
        spend_cap: Account-level spend cap in cents. Stops the campaign when reached.
        start_time: Campaign start time in ISO 8601, e.g. '2026-05-01T00:00:00+0700'.
        stop_time: Campaign end time in ISO 8601. Required with lifetime_budget.
        bid_strategy: Bid strategy for CBO campaigns. Values:
            LOWEST_COST_WITHOUT_CAP (default — Meta picks best bid)
            LOWEST_COST_WITH_BID_CAP — set a max bid via adset bid_amount
            COST_CAP — target average cost
        buying_type: Ad buying model. Values:
            AUCTION (default — standard bidding)
            RESERVED — Reach & Frequency, requires prior reservation
        smart_promotion_type: Enable Meta AI automation. Values:
            AUTOMATED_SHOPPING_ADS — Advantage+ Shopping Campaign (ASC)
            Omit for standard campaigns.

    Returns:
        Dict with 'id' of the newly created campaign and 'success' boolean.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    params: Dict[str, Any] = {
        "name": name,
        "objective": objective,
        "status": status,
        "special_ad_categories": json.dumps(special_ad_categories if special_ad_categories is not None else []),
    }
    if daily_budget is not None:
        params["daily_budget"] = daily_budget
    if lifetime_budget is not None:
        params["lifetime_budget"] = lifetime_budget
    if spend_cap is not None:
        params["spend_cap"] = spend_cap
    if start_time:
        params["start_time"] = start_time
    if stop_time:
        params["stop_time"] = stop_time
    if bid_strategy:
        params["bid_strategy"] = bid_strategy
    if buying_type:
        params["buying_type"] = buying_type
    if smart_promotion_type:
        params["smart_promotion_type"] = smart_promotion_type

    result = account.create_campaign(params=params)
    return _to_dict(result)


@mcp.tool()
def update_campaign(
    campaign_id: str,
    name: Optional[str] = None,
    status: Optional[str] = None,
    daily_budget: Optional[int] = None,
    lifetime_budget: Optional[int] = None,
    spend_cap: Optional[int] = None,
    start_time: Optional[str] = None,
    stop_time: Optional[str] = None,
    bid_strategy: Optional[str] = None,
) -> Dict:
    """Update an existing campaign: name, status, budget, time range, bid strategy.

    Only the fields you provide will be changed — omit any field to leave it unchanged.

    Args:
        campaign_id: The campaign ID to update, e.g. '120243715018340308'.
        name: New campaign name.
        status: New status. Values: ACTIVE, PAUSED, ARCHIVED, DELETED.
        daily_budget: New daily budget in cents. Only valid for CBO campaigns.
        lifetime_budget: New lifetime budget in cents.
        spend_cap: New account spend cap in cents.
        start_time: New start time in ISO 8601 format.
        stop_time: New end time in ISO 8601 format.
        bid_strategy: New bid strategy. Values: LOWEST_COST_WITHOUT_CAP,
            LOWEST_COST_WITH_BID_CAP, COST_CAP.

    Returns:
        Dict with 'success' boolean and 'updated_id'.
    """
    _init_sdk()
    campaign = Campaign(fbid=campaign_id)
    params: Dict[str, Any] = {}
    if name is not None:
        params["name"] = name
    if status is not None:
        params["status"] = status
    if daily_budget is not None:
        params["daily_budget"] = daily_budget
    if lifetime_budget is not None:
        params["lifetime_budget"] = lifetime_budget
    if spend_cap is not None:
        params["spend_cap"] = spend_cap
    if start_time is not None:
        params["start_time"] = start_time
    if stop_time is not None:
        params["stop_time"] = stop_time
    if bid_strategy is not None:
        params["bid_strategy"] = bid_strategy
    campaign.api_update(params=params)
    return {"success": True, "updated_id": campaign_id}


@mcp.tool()
def create_adset(
    act_id: str,
    name: str,
    campaign_id: str,
    optimization_goal: str,
    billing_event: str,
    targeting: Dict,
    status: Optional[str] = "PAUSED",
    daily_budget: Optional[int] = None,
    lifetime_budget: Optional[int] = None,
    daily_spend_cap: Optional[int] = None,
    lifetime_spend_cap: Optional[int] = None,
    bid_amount: Optional[int] = None,
    bid_strategy: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    destination_type: Optional[str] = None,
    promoted_object: Optional[Dict] = None,
    pacing_type: Optional[List[str]] = None,
    is_dynamic_creative: Optional[bool] = None,
    frequency_control_specs: Optional[List[Dict]] = None,
    attribution_spec: Optional[List[Dict]] = None,
    dsa_beneficiary: Optional[str] = None,
    dsa_payor: Optional[str] = None,
    existing_customer_budget_percentage: Optional[int] = None,
    tune_for_category: Optional[str] = None,
) -> Dict:
    """Create a new ad set within a campaign with targeting, optimization, and budget.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Ad set name.
        campaign_id: Parent campaign ID.
        optimization_goal: What Meta optimizes delivery toward. Common values:
            LINK_CLICKS, IMPRESSIONS, REACH, LANDING_PAGE_VIEWS,
            APP_INSTALLS, LEAD_GENERATION, OFFSITE_CONVERSIONS,
            THRUPLAY (video), POST_ENGAGEMENT, QUALITY_LEAD.
        billing_event: When you are charged. Values: IMPRESSIONS, LINK_CLICKS.
        targeting: Targeting spec dict. Minimum example:
            {"geo_locations": {"countries": ["VN"]}, "age_min": 18, "age_max": 65}
            With interests: {"geo_locations": ..., "interests": [{"id": "6003161134183"}]}
        status: 'PAUSED' (default) or 'ACTIVE'.
        daily_budget: Daily budget in cents. Required unless parent campaign uses CBO.
        lifetime_budget: Lifetime budget in cents. Mutually exclusive with daily_budget.
        daily_spend_cap: Max daily spend in cents (overrides daily_budget ceiling).
        lifetime_spend_cap: Max lifetime spend in cents.
        bid_amount: Manual bid in cents. Required when bid_strategy = LOWEST_COST_WITH_BID_CAP.
        bid_strategy: Values: LOWEST_COST_WITHOUT_CAP, LOWEST_COST_WITH_BID_CAP, COST_CAP.
        start_time: Start time in ISO 8601 format.
        end_time: End time in ISO 8601. Required when using lifetime_budget.
        destination_type: Where users land after clicking. Values:
            WEBSITE (default), APP, MESSENGER, WHATSAPP, INSTAGRAM_DIRECT,
            MESSAGING_MESSENGER_WHATSAPP, MESSAGING_INSTAGRAM_DIRECT_MESSENGER,
            MESSAGING_INSTAGRAM_DIRECT_WHATSAPP,
            MESSAGING_INSTAGRAM_DIRECT_MESSENGER_WHATSAPP,
            SHOP_AUTOMATIC, APPLINKS_AUTOMATIC,
            ON_AD, ON_POST, ON_PAGE, ON_VIDEO, ON_EVENT,
            INSTAGRAM_PROFILE, FACEBOOK.
        promoted_object: Required for conversion and app campaigns. Dict specifying
            the conversion event source. Examples:
            Website conversion:   {"pixel_id": "123", "custom_event_type": "PURCHASE"}
            App install:          {"application_id": "456", "object_store_url": "https://..."}
            Lead gen (page):      {"page_id": "789"}
            Catalog sales:        {"product_set_id": "101"}
            Custom event types: PURCHASE, ADD_TO_CART, LEAD, COMPLETE_REGISTRATION,
                INITIATED_CHECKOUT, SEARCH, VIEW_CONTENT, CONTACT, SUBSCRIBE.
        pacing_type: Delivery pacing. Values: ["STANDARD"] (default even pacing)
            or ["NO_PACING"] (spend budget as fast as possible).
        is_dynamic_creative: Set True to enable Dynamic Creative Optimization (DCO).
            Requires asset_feed_spec in the ad creative.
        frequency_control_specs: Cap how often users see your ad. Example:
            [{"event": "IMPRESSIONS", "interval_days": 7, "max_frequency": 2}]
        attribution_spec: Custom attribution windows. Example:
            [{"event_type": "CLICK", "window_days": 7},
             {"event_type": "VIEW", "window_days": 1}]
        dsa_beneficiary: EU Digital Services Act — name of the entity paying for the ad.
            Required for ads shown in the EU. E.g. 'Chotot Vietnam'.
        dsa_payor: EU DSA — name of entity paying. Often same as dsa_beneficiary.
        existing_customer_budget_percentage: For Advantage+ Shopping — % of budget
            allocated to existing customers (0–100). Default 30 (Meta recommended).
        tune_for_category: Tune delivery for a vertical. Values:
            NONE (default), SHOPPING, HOUSING_RENTAL, CREDIT,
            EMPLOYMENT, POLITICAL_AND_SOCIAL_ISSUES.

    Returns:
        Dict with 'id' of the newly created ad set.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    params: Dict[str, Any] = {
        "name": name,
        "campaign_id": campaign_id,
        "optimization_goal": optimization_goal,
        "billing_event": billing_event,
        "targeting": json.dumps(targeting),
        "status": status,
    }
    if daily_budget is not None:
        params["daily_budget"] = daily_budget
    if lifetime_budget is not None:
        params["lifetime_budget"] = lifetime_budget
    if daily_spend_cap is not None:
        params["daily_spend_cap"] = daily_spend_cap
    if lifetime_spend_cap is not None:
        params["lifetime_spend_cap"] = lifetime_spend_cap
    if bid_amount is not None:
        params["bid_amount"] = bid_amount
    if bid_strategy is not None:
        params["bid_strategy"] = bid_strategy
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    if destination_type is not None:
        params["destination_type"] = destination_type
    if promoted_object is not None:
        params["promoted_object"] = json.dumps(promoted_object)
    if pacing_type is not None:
        params["pacing_type"] = json.dumps(pacing_type)
    if is_dynamic_creative is not None:
        params["is_dynamic_creative"] = is_dynamic_creative
    if frequency_control_specs is not None:
        params["frequency_control_specs"] = json.dumps(frequency_control_specs)
    if attribution_spec is not None:
        params["attribution_spec"] = json.dumps(attribution_spec)
    if dsa_beneficiary is not None:
        params["dsa_beneficiary"] = dsa_beneficiary
    if dsa_payor is not None:
        params["dsa_payor"] = dsa_payor
    if existing_customer_budget_percentage is not None:
        params["existing_customer_budget_percentage"] = existing_customer_budget_percentage
    if tune_for_category is not None:
        params["tune_for_category"] = tune_for_category

    result = account.create_ad_set(params=params)
    return _to_dict(result)


@mcp.tool()
def update_adset(
    adset_id: str,
    name: Optional[str] = None,
    status: Optional[str] = None,
    daily_budget: Optional[int] = None,
    lifetime_budget: Optional[int] = None,
    daily_spend_cap: Optional[int] = None,
    lifetime_spend_cap: Optional[int] = None,
    bid_amount: Optional[int] = None,
    bid_strategy: Optional[str] = None,
    optimization_goal: Optional[str] = None,
    targeting: Optional[Dict] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    destination_type: Optional[str] = None,
    promoted_object: Optional[Dict] = None,
    pacing_type: Optional[List[str]] = None,
    is_dynamic_creative: Optional[bool] = None,
    frequency_control_specs: Optional[List[Dict]] = None,
    attribution_spec: Optional[List[Dict]] = None,
    dsa_beneficiary: Optional[str] = None,
    dsa_payor: Optional[str] = None,
    existing_customer_budget_percentage: Optional[int] = None,
    tune_for_category: Optional[str] = None,
) -> Dict:
    """Update an existing ad set: name, status, budget, bidding, optimization, targeting.

    Only the fields you provide will be changed — omit any field to leave it unchanged.

    Args:
        adset_id: The ad set ID to update.
        name: New ad set name.
        status: New status. Values: ACTIVE, PAUSED, ARCHIVED, DELETED.
        daily_budget: New daily budget in cents.
        lifetime_budget: New lifetime budget in cents.
        daily_spend_cap: New max daily spend in cents.
        lifetime_spend_cap: New max lifetime spend in cents.
        bid_amount: New manual bid in cents.
        bid_strategy: New bid strategy: LOWEST_COST_WITHOUT_CAP,
            LOWEST_COST_WITH_BID_CAP, COST_CAP.
        optimization_goal: New optimization goal.
        targeting: New targeting spec dict (replaces existing targeting entirely).
        start_time: New start time in ISO 8601 format.
        end_time: New end time in ISO 8601 format.
        destination_type: New destination type (see create_adset for full values list).
        promoted_object: New promoted object dict (see create_adset for examples).
        pacing_type: New pacing. ["STANDARD"] or ["NO_PACING"].
        is_dynamic_creative: Enable/disable Dynamic Creative Optimization.
        frequency_control_specs: New frequency cap specs list.
        attribution_spec: New attribution window specs list.
        dsa_beneficiary: EU DSA beneficiary name.
        dsa_payor: EU DSA payor name.
        existing_customer_budget_percentage: Advantage+ Shopping customer budget split.
        tune_for_category: Vertical tuning category.

    Returns:
        Dict with 'success' boolean and 'updated_id'.
    """
    _init_sdk()
    adset = AdSet(fbid=adset_id)
    params: Dict[str, Any] = {}
    if name is not None:
        params["name"] = name
    if status is not None:
        params["status"] = status
    if daily_budget is not None:
        params["daily_budget"] = daily_budget
    if lifetime_budget is not None:
        params["lifetime_budget"] = lifetime_budget
    if daily_spend_cap is not None:
        params["daily_spend_cap"] = daily_spend_cap
    if lifetime_spend_cap is not None:
        params["lifetime_spend_cap"] = lifetime_spend_cap
    if bid_amount is not None:
        params["bid_amount"] = bid_amount
    if bid_strategy is not None:
        params["bid_strategy"] = bid_strategy
    if optimization_goal is not None:
        params["optimization_goal"] = optimization_goal
    if targeting is not None:
        params["targeting"] = json.dumps(targeting)
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if destination_type is not None:
        params["destination_type"] = destination_type
    if promoted_object is not None:
        params["promoted_object"] = json.dumps(promoted_object)
    if pacing_type is not None:
        params["pacing_type"] = json.dumps(pacing_type)
    if is_dynamic_creative is not None:
        params["is_dynamic_creative"] = is_dynamic_creative
    if frequency_control_specs is not None:
        params["frequency_control_specs"] = json.dumps(frequency_control_specs)
    if attribution_spec is not None:
        params["attribution_spec"] = json.dumps(attribution_spec)
    if dsa_beneficiary is not None:
        params["dsa_beneficiary"] = dsa_beneficiary
    if dsa_payor is not None:
        params["dsa_payor"] = dsa_payor
    if existing_customer_budget_percentage is not None:
        params["existing_customer_budget_percentage"] = existing_customer_budget_percentage
    if tune_for_category is not None:
        params["tune_for_category"] = tune_for_category
    adset.api_update(params=params)
    return {"success": True, "updated_id": adset_id}


@mcp.tool()
def create_ad_creative(
    act_id: str,
    name: str,
    object_story_id: Optional[str] = None,
    object_story_spec: Optional[Dict] = None,
    asset_feed_spec: Optional[Dict] = None,
    degrees_of_freedom_spec: Optional[Dict] = None,
    url_tags: Optional[str] = None,
    call_to_action_type: Optional[str] = None,
) -> Dict:
    """Create a new ad creative (image or video with copy). Creatives are immutable after creation.

    To change a creative after creation, create a new one and use update_ad to swap it.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Creative name (internal label, not shown to users).
        object_story_id: ID of an existing published page post to use as the ad creative.
            Format: '{page_id}_{post_id}', e.g. '300829936671250_1234567890'.
            Use this to boost an existing page post. Mutually exclusive with object_story_spec.
        object_story_spec: Story spec for standard single-image or single-video creatives.
            Example — link ad with image:
            {
              "page_id": "123456789",
              "link_data": {
                "link": "https://www.chotot.com",
                "message": "Your ad copy here",
                "name": "Headline text",
                "description": "Body description",
                "image_hash": "abc123hash"
              }
            }
            Example — video ad:
            {
              "page_id": "123456789",
              "video_data": {
                "video_id": "987654321",
                "title": "Video headline",
                "message": "Ad copy text",
                "call_to_action": {"type": "LEARN_MORE", "value": {"link": "https://..."}}
              }
            }
            Omit when using asset_feed_spec (DCO / multi-asset creatives).
        asset_feed_spec: Multi-asset spec for Dynamic Creative Optimization (DCO).
            Provide multiple headlines, bodies, images — Meta tests combinations.
            Requires is_dynamic_creative=True on the ad set.
            Example:
            {
              "bodies": [{"text": "Copy A"}, {"text": "Copy B"}],
              "titles": [{"text": "Headline A"}, {"text": "Headline B"}],
              "images": [{"hash": "abc123"}, {"hash": "def456"}],
              "link_urls": [{"website_url": "https://chotot.com"}],
              "call_to_action_types": ["LEARN_MORE", "SHOP_NOW"]
            }
        degrees_of_freedom_spec: Advantage+ Creative settings — Meta AI auto-enhances
            the creative to improve performance. Example:
            {
              "creative_features_spec": {
                "standard_enhancements": {"enroll_status": "OPT_IN"}
              }
            }
        url_tags: UTM parameters appended to destination URL. Example:
            'utm_source=facebook&utm_medium=paid&utm_campaign={{campaign.name}}'
        call_to_action_type: Override CTA button. Values:
            LEARN_MORE, SHOP_NOW, SIGN_UP, BOOK_TRAVEL, DOWNLOAD,
            CONTACT_US, GET_QUOTE, APPLY_NOW, SUBSCRIBE, WATCH_MORE.

    Returns:
        Dict with 'id' of the newly created creative.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    params: Dict[str, Any] = {"name": name}
    if object_story_id is not None:
        params["object_story_id"] = object_story_id
    if object_story_spec is not None:
        params["object_story_spec"] = json.dumps(object_story_spec)
    if asset_feed_spec is not None:
        params["asset_feed_spec"] = json.dumps(asset_feed_spec)
    if degrees_of_freedom_spec is not None:
        params["degrees_of_freedom_spec"] = json.dumps(degrees_of_freedom_spec)
    if url_tags is not None:
        params["url_tags"] = url_tags
    if call_to_action_type is not None:
        params["call_to_action_type"] = call_to_action_type

    result = account.create_ad_creative(params=params)
    return _to_dict(result)


@mcp.tool()
def create_ad(
    act_id: str,
    name: str,
    adset_id: str,
    creative_id: str,
    status: Optional[str] = "PAUSED",
    tracking_specs: Optional[List[Dict]] = None,
    audience_id: Optional[str] = None,
) -> Dict:
    """Create a new ad by linking an ad creative to an ad set.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Ad name.
        adset_id: Parent ad set ID.
        creative_id: ID of the ad creative to use. Get it from create_ad_creative().
        status: 'PAUSED' (default) or 'ACTIVE'.
        tracking_specs: Pixel/app event tracking configurations. Example for pixel:
            [{"action.type": ["offsite_conversion"],
              "fb_pixel": ["<pixel_id>"]}]
        audience_id: Custom audience ID to attach directly to this ad (ad-level targeting).

    Returns:
        Dict with 'id' of the newly created ad.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    params: Dict[str, Any] = {
        "name": name,
        "adset_id": adset_id,
        "creative": json.dumps({"creative_id": creative_id}),
        "status": status,
    }
    if tracking_specs is not None:
        params["tracking_specs"] = json.dumps(tracking_specs)
    if audience_id is not None:
        params["audience_id"] = audience_id

    result = account.create_ad(params=params)
    return _to_dict(result)


@mcp.tool()
def update_ad(
    ad_id: str,
    name: Optional[str] = None,
    status: Optional[str] = None,
    creative_id: Optional[str] = None,
    tracking_specs: Optional[List[Dict]] = None,
) -> Dict:
    """Update an existing ad: name, status, or swaps to a new creative.

    To change the creative, provide a new creative_id. The old creative is NOT deleted.

    Args:
        ad_id: The ad ID to update.
        name: New ad name.
        status: New status. Values: ACTIVE, PAUSED, ARCHIVED, DELETED.
        creative_id: ID of the new creative to swap in.
            Create a new creative first via create_ad_creative(), then pass its ID here.
        tracking_specs: New pixel/app event tracking configuration.
            Example: [{"action.type": ["offsite_conversion"], "fb_pixel": ["<pixel_id>"]}]

    Returns:
        Dict with 'success' boolean and 'updated_id'.
    """
    _init_sdk()
    ad = Ad(fbid=ad_id)
    params: Dict[str, Any] = {}
    if name is not None:
        params["name"] = name
    if status is not None:
        params["status"] = status
    if creative_id is not None:
        params["creative"] = json.dumps({"creative_id": creative_id})
    if tracking_specs is not None:
        params["tracking_specs"] = json.dumps(tracking_specs)
    ad.api_update(params=params)
    return {"success": True, "updated_id": ad_id}


# ─────────────────────────────────────────────────────────────────────────────
# 10. PIXELS & CUSTOM CONVERSIONS
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_pixels(
    act_id: str,
    fields: Optional[List[str]] = None,
) -> Dict:
    """List all Meta Pixels owned by an ad account.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        fields: Fields to return. Default: id, name, code, creation_time, last_fired_time.

    Returns:
        Dict with 'data' list of pixels.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    default_fields = ["id", "name", "code", "creation_time", "last_fired_time", "is_unavailable"]
    pixels = account.get_ads_pixels(fields=fields or default_fields)
    return {"data": _edge(pixels)}


@mcp.tool()
def create_pixel(act_id: str, name: str) -> Dict:
    """Create a new Meta Pixel for an ad account.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Pixel name, e.g. 'Chotot Main Pixel'.

    Returns:
        Dict with 'id' and 'code' (the pixel JavaScript snippet).
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    pixel = account.create_ads_pixel(params={"name": name})
    return _to_dict(pixel)


@mcp.tool()
def get_pixel_stats(
    pixel_id: str,
    start_time: str,
    end_time: str,
    aggregation: Optional[str] = "day",
) -> Dict:
    """Get event volume stats for a pixel over a time range.

    Args:
        pixel_id: Pixel ID.
        start_time: Start of range as Unix timestamp string or ISO 8601 date, e.g. '2026-04-01'.
        end_time: End of range, same format.
        aggregation: Time bucket. Values: 'day' (default) or 'hour'.

    Returns:
        Dict with 'data' list — each row has 'timestamp' and event counts by type
        (PageView, Purchase, AddToCart, Lead, etc.).
    """
    _init_sdk()
    pixel = AdsPixel(fbid=pixel_id)
    stats = pixel.get_stats(params={
        "start_time": start_time,
        "end_time": end_time,
        "aggregation": aggregation,
    })
    return {"data": _edge(stats)}


@mcp.tool()
def get_custom_conversions(
    act_id: str,
    fields: Optional[List[str]] = None,
) -> Dict:
    """List all custom conversions defined in an ad account.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        fields: Fields to return. Default: id, name, pixel, rule, custom_event_type,
            creation_time, last_fired_time, stats.

    Returns:
        Dict with 'data' list of custom conversions.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    default_fields = [
        "id", "name", "pixel", "rule", "custom_event_type",
        "creation_time", "last_fired_time",
    ]
    conversions = account.get_custom_conversions(fields=fields or default_fields)
    return {"data": _edge(conversions)}


@mcp.tool()
def create_custom_conversion(
    act_id: str,
    name: str,
    pixel_id: str,
    custom_event_type: str,
    rule: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict:
    """Create a custom conversion event tied to a pixel.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Conversion name, e.g. 'Purchase > 500k VND'.
        pixel_id: Pixel ID to attach the conversion to.
        custom_event_type: Base event to track. Values:
            PURCHASE, ADD_TO_CART, LEAD, COMPLETE_REGISTRATION,
            INITIATED_CHECKOUT, SEARCH, VIEW_CONTENT, CONTACT, SUBSCRIBE,
            ADD_TO_WISHLIST, OTHER.
        rule: JSON string filter to narrow which events count. Example:
            '{"and": [{"event_sources": [{"id": "<pixel_id>", "type": "pixel"}]},
              {"url": {"contains": "/checkout/confirm"}}]}'
            Omit to track all events of custom_event_type.
        description: Optional description for this conversion.

    Returns:
        Dict with 'id' of the created custom conversion.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    params: Dict[str, Any] = {
        "name": name,
        "pixel_id": pixel_id,
        "custom_event_type": custom_event_type,
    }
    if rule:
        params["rule"] = rule
    if description:
        params["description"] = description
    result = account.create_custom_conversion(params=params)
    return _to_dict(result)


# ─────────────────────────────────────────────────────────────────────────────
# 11. BUDGET SCHEDULES (DAYPARTING)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_budget_schedules(campaign_id: str) -> Dict:
    """List all budget schedules (dayparting rules) for a campaign.

    Budget schedules temporarily override the campaign's budget during a time window.
    Use them to increase spend during peak hours or reduce it overnight.

    Args:
        campaign_id: Campaign ID.

    Returns:
        Dict with 'data' list of schedules — each has 'id', 'budget_value',
        'budget_value_type', 'time_start', 'time_end', 'recurrence_type'.
    """
    _init_sdk()
    result = _get(
        f"{campaign_id}/budget_schedules",
        params={"fields": "id,budget_value,budget_value_type,time_start,time_end,recurrence_type,status"},
    )
    return result


@mcp.tool()
def create_budget_schedule(
    campaign_id: str,
    time_start: int,
    time_end: int,
    budget_value: int,
    budget_value_type: str,
) -> Dict:
    """Create a budget schedule (daypart) on a campaign.

    The schedule temporarily overrides the campaign's budget during the specified window.

    Args:
        campaign_id: Campaign ID to add the schedule to.
        time_start: Window start as Unix timestamp (seconds), e.g. 1746057600.
        time_end: Window end as Unix timestamp (seconds). Must be after time_start.
        budget_value: Budget override value.
            If budget_value_type=ABSOLUTE: amount in cents (e.g. 5000000 = $50,000 VND).
            If budget_value_type=MULTIPLIER: factor × 100 (e.g. 150 = 1.5× the base budget).
        budget_value_type: How budget_value is interpreted.
            ABSOLUTE — replace budget with this exact amount during the window.
            MULTIPLIER — multiply the base budget by (budget_value / 100).

    Returns:
        Dict with 'id' of the created budget schedule.
    """
    _init_sdk()
    result = _post(
        f"{campaign_id}/budget_schedules",
        {
            "time_start": time_start,
            "time_end": time_end,
            "budget_value": budget_value,
            "budget_value_type": budget_value_type,
        },
    )
    return result


@mcp.tool()
def delete_budget_schedule(budget_schedule_id: str) -> Dict:
    """Delete a campaign budget schedule.

    Args:
        budget_schedule_id: Budget schedule ID returned by get_budget_schedules or create_budget_schedule.

    Returns:
        Dict with 'success' boolean.
    """
    _init_sdk()
    result = _delete(budget_schedule_id)
    return {"success": result.get("success", True), "deleted_id": budget_schedule_id}


# ─────────────────────────────────────────────────────────────────────────────
# 12. SAVED AUDIENCES
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_saved_audiences(
    act_id: str,
    fields: Optional[List[str]] = None,
    limit: Optional[int] = 50,
) -> Dict:
    """List saved audiences (stored targeting specs) in an ad account.

    Saved audiences are reusable targeting specs created in Ads Manager.
    Use their targeting spec directly in create_adset to avoid re-specifying targeting.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        fields: Fields to return. Default: id, name, targeting, approximate_count,
            description, time_created, time_updated.
        limit: Max results. Default 50.

    Returns:
        Dict with 'data' list of saved audiences including their full targeting spec.
    """
    _init_sdk()
    account = AdAccount(fbid=act_id)
    default_fields = [
        "id", "name", "targeting", "approximate_count",
        "description", "time_created", "time_updated",
    ]
    audiences = account.get_saved_audiences(
        fields=fields or default_fields,
        params={"limit": limit},
    )
    return {"data": _edge(audiences, max_items=limit)}


@mcp.tool()
def get_saved_audience(
    audience_id: str,
    fields: Optional[List[str]] = None,
) -> Dict:
    """Get the full details of a specific saved audience including its targeting spec.

    Args:
        audience_id: Saved audience ID (from get_saved_audiences).
        fields: Fields to return. Default: id, name, targeting, approximate_count, description.

    Returns:
        Dict with audience details. The 'targeting' field contains the full spec
        you can pass directly to create_adset's targeting parameter.
    """
    _init_sdk()
    audience = SavedAudience(fbid=audience_id)
    default_fields = ["id", "name", "targeting", "approximate_count", "description"]
    return _to_dict(audience.api_get(fields=fields or default_fields))


# ─────────────────────────────────────────────────────────────────────────────
# 13. PRODUCT FEEDS
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_product_feeds(catalog_id: str) -> Dict:
    """List all product feeds in a catalog.

    Product feeds are URL-based or manual upload pipelines that keep catalog products
    in sync with your data source.

    Args:
        catalog_id: Product catalog ID.

    Returns:
        Dict with 'data' list of feeds — each has 'id', 'name', 'file_name',
        'ingestion_source_type', 'schedule', 'latest_upload' (status, num_detected, errors).
    """
    _init_sdk()
    catalog = ProductCatalog(fbid=catalog_id)
    feeds = catalog.get_product_feeds(fields=[
        "id", "name", "file_name", "ingestion_source_type",
        "schedule", "latest_upload", "country", "override_type",
    ])
    return {"data": _edge(feeds)}


@mcp.tool()
def create_product_feed(
    catalog_id: str,
    name: str,
    url: Optional[str] = None,
    schedule: Optional[Dict] = None,
) -> Dict:
    """Create a product feed in a catalog.

    Args:
        catalog_id: Target product catalog ID.
        name: Feed name, e.g. 'Chotot Daily Feed'.
        url: Public URL of the feed file (CSV, TSV, XML/RSS). Required for scheduled fetch.
            The URL must be publicly accessible without authentication.
        schedule: Auto-fetch schedule dict. Example:
            {
              "interval": "DAILY",   # HOURLY | DAILY | WEEKLY
              "url": "https://yoursite.com/feed.csv",
              "hour": "6"            # Hour in UTC (0–23) for DAILY/WEEKLY
            }
            Omit url and schedule to create a manual-upload feed.

    Returns:
        Dict with 'id' of the created feed.
    """
    _init_sdk()
    catalog = ProductCatalog(fbid=catalog_id)
    params: Dict[str, Any] = {"name": name}
    if url:
        params["url"] = url
    if schedule:
        params["schedule"] = json.dumps(schedule)
    result = catalog.create_product_feed(params=params)
    return _to_dict(result)


@mcp.tool()
def update_product_feed(
    feed_id: str,
    name: Optional[str] = None,
    url: Optional[str] = None,
    schedule: Optional[Dict] = None,
) -> Dict:
    """Update name, URL, or schedule of an existing product feed.

    Only the fields you provide will be changed.

    Args:
        feed_id: Product feed ID to update.
        name: New feed name.
        url: New feed file URL.
        schedule: New fetch schedule dict (same format as create_product_feed).

    Returns:
        Dict with 'success' boolean.
    """
    _init_sdk()
    feed = ProductFeed(fbid=feed_id)
    params: Dict[str, Any] = {}
    if name is not None:
        params["name"] = name
    if url is not None:
        params["url"] = url
    if schedule is not None:
        params["schedule"] = json.dumps(schedule)
    feed.api_update(params=params)
    return {"success": True, "updated_id": feed_id}


@mcp.tool()
def delete_product_feed(feed_id: str) -> Dict:
    """Delete a product feed from its catalog.

    Args:
        feed_id: Product feed ID to delete.

    Returns:
        Dict with 'success' boolean.
    """
    _init_sdk()
    feed = ProductFeed(fbid=feed_id)
    feed.api_delete()
    return {"success": True, "deleted_id": feed_id}


# ─────────────────────────────────────────────────────────────────────────────
# 14. SPLIT TESTS (A/B TESTING)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_split_tests(
    act_id: str,
    limit: Optional[int] = 25,
) -> Dict:
    """List A/B split tests (ad studies) for an ad account.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        limit: Max results. Default 25.

    Returns:
        Dict with 'data' list of split tests — each has 'id', 'name', 'type',
        'status', 'start_time', 'end_time', 'cells' (campaigns/adsets being compared).
    """
    _init_sdk()
    result = _get(
        f"{act_id}/ad_studies",
        params={
            "fields": "id,name,type,status,start_time,end_time,cells{campaigns,adsets,name,percent_traffic}",
            "limit": limit,
        },
    )
    return result


@mcp.tool()
def create_split_test(
    act_id: str,
    name: str,
    start_time: int,
    end_time: int,
    kpi_type: str,
    cell_specs: List[Dict],
    confidence_level: Optional[float] = 0.95,
    kpi_custom_conversion_id: Optional[str] = None,
) -> Dict:
    """Create an A/B split test between two campaigns or ad sets.

    A split test divides your audience and budget between cells to measure
    which ad approach performs better with statistical significance.

    Args:
        act_id: Ad account ID prefixed with 'act_'.
        name: Test name, e.g. 'Creative A vs B — May 2026'.
        start_time: Test start as Unix timestamp (seconds).
        end_time: Test end as Unix timestamp (seconds). Minimum 3 days recommended.
        kpi_type: Metric to optimize and measure significance for. Values:
            LINK_CLICKS, REACH, IMPRESSIONS, OFFSITE_CONVERSIONS,
            LANDING_PAGE_VIEWS, POST_ENGAGEMENT, APP_INSTALLS,
            THRUPLAY, CUSTOM_CONVERSION (requires kpi_custom_conversion_id).
        cell_specs: List of 2 cells to compare. Each cell is a dict:
            {
              "name": "Control",                   # Cell label
              "campaigns": ["campaign_id_1"],       # OR use "adsets" key
              "percent_traffic": 50                 # Traffic split (must sum to 100)
            }
            Example with 2 campaigns:
            [
              {"name": "Image Ad", "campaigns": ["123"], "percent_traffic": 50},
              {"name": "Video Ad", "campaigns": ["456"], "percent_traffic": 50}
            ]
        confidence_level: Statistical confidence threshold (0.0–1.0). Default 0.95 (95%).
            Test declares a winner only when this confidence is reached.
        kpi_custom_conversion_id: Required when kpi_type='CUSTOM_CONVERSION'.
            ID from create_custom_conversion or get_custom_conversions.

    Returns:
        Dict with 'id' of the created split test.
    """
    _init_sdk()
    payload: Dict[str, Any] = {
        "name": name,
        "start_time": start_time,
        "end_time": end_time,
        "type": "SPLIT_TEST",
        "confidence_level": confidence_level,
        "kpi_type": kpi_type,
        "cells": json.dumps(cell_specs),
    }
    if kpi_custom_conversion_id:
        payload["kpi_custom_conversion_id"] = kpi_custom_conversion_id
    result = _post(f"{act_id}/ad_studies", payload)
    return result


@mcp.tool()
def get_split_test(split_test_id: str) -> Dict:
    """Get the details and current results of a split test.

    Args:
        split_test_id: Split test (ad study) ID from create_split_test or get_split_tests.

    Returns:
        Dict with full test details including 'status', 'cells', and 'results'
        (winner declared, confidence reached, KPI values per cell).
    """
    _init_sdk()
    result = _get(
        split_test_id,
        params={
            "fields": (
                "id,name,type,status,start_time,end_time,confidence_level,kpi_type,"
                "cells{name,campaigns,adsets,percent_traffic},"
                "results{cell,kpi_value,confidence_level}"
            )
        },
    )
    return result


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _get_fb_access_token()
    mcp.run(transport='stdio')
