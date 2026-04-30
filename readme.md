# Facebook Ads MCP Server

An MCP (Model Context Protocol) server for the Facebook Marketing API v22.0, enabling Claude Desktop to manage Facebook ad campaigns, audiences, creatives, pixels, catalogs, and more through natural language.

**78 tools** across two files — read-only operations in `server.py`, extended SDK + write operations in `server_sdk.py` (entry point).

---

## Setup

### Prerequisites

- Python 3.10+
- A Facebook access token with Marketing API permissions ([how to get one](https://developers.facebook.com/docs/marketing-apis/get-started))

### Install

```bash
# 1. Clone the repo
git clone https://github.com/dattruong-mkt/facebook-ads-mcp-server.git
cd facebook-ads-mcp-server

# 2. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Configure Claude Desktop

Add the following to your Claude Desktop config file:

**Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "fb-ads": {
      "command": "/full/path/to/facebook-ads-mcp-server/venv/bin/python3",
      "args": [
        "/full/path/to/facebook-ads-mcp-server/server_sdk.py",
        "--fb-token",
        "YOUR_FB_ACCESS_TOKEN"
      ]
    }
  }
}
```

Replace `/full/path/to/` with the actual path where you cloned the repo. Restart Claude Desktop after saving.

### Verify

After restarting Claude Desktop, you should see 78 tools available. You can ask Claude: *"List my Facebook ad accounts"* to confirm the connection works.

---

## Available Tools (78)

### Account & Object Read (21 tools — `server.py`)

| Tool | Description |
|------|-------------|
| `list_ad_accounts` | List ad accounts linked to the token |
| `get_details_of_ad_account` | Get details for a specific ad account |
| `get_campaign_by_id` | Get details for a specific campaign |
| `get_campaigns_by_adaccount` | Get all campaigns in an ad account |
| `get_adset_by_id` | Get details for a specific ad set |
| `get_adsets_by_ids` | Get details for multiple ad sets by ID |
| `get_adsets_by_adaccount` | Get all ad sets in an ad account |
| `get_adsets_by_campaign` | Get all ad sets in a campaign |
| `get_ad_by_id` | Get details for a specific ad |
| `get_ads_by_adaccount` | Get all ads in an ad account |
| `get_ads_by_campaign` | Get all ads in a campaign |
| `get_ads_by_adset` | Get all ads in an ad set |
| `get_ad_creative_by_id` | Get details for a specific creative |
| `get_ad_creatives_by_ad_id` | Get all creatives for an ad |
| `get_adaccount_insights` | Get performance insights for an ad account |
| `get_campaign_insights` | Get performance insights for a campaign |
| `get_adset_insights` | Get performance insights for an ad set |
| `get_ad_insights` | Get performance insights for an ad |
| `get_activities_by_adaccount` | Get change history for an ad account |
| `get_activities_by_adset` | Get change history for an ad set |
| `fetch_pagination_url` | Fetch data from a pagination cursor URL |

### Campaign, Ad Set & Ad Management (7 tools)

| Tool | Description |
|------|-------------|
| `create_campaign` | Create a new campaign |
| `update_campaign` | Update an existing campaign |
| `create_adset` | Create a new ad set with full targeting |
| `update_adset` | Update an existing ad set |
| `create_ad_creative` | Create a new ad creative (image, video, or DCO) |
| `create_ad` | Create a new ad |
| `update_ad` | Update an existing ad |

### Copy Operations (3 tools)

| Tool | Description |
|------|-------------|
| `copy_campaign` | Duplicate a campaign |
| `copy_adset` | Duplicate an ad set |
| `copy_ad` | Duplicate an ad |

### Audiences (6 tools)

| Tool | Description |
|------|-------------|
| `get_audiences` | List custom audiences in an ad account |
| `create_custom_audience` | Create a custom audience |
| `upload_custom_audience_users` | Upload users to a custom audience |
| `create_lookalike_audience` | Create a lookalike audience from a source |
| `get_reach_estimate` | Estimate reach for a targeting spec |
| `delete_audience` | Delete a custom audience |

### Saved Audiences (2 tools)

| Tool | Description |
|------|-------------|
| `get_saved_audiences` | List saved audiences in an ad account |
| `get_saved_audience` | Get a saved audience's targeting spec (paste directly into create_adset) |

### Insights with Breakdown (1 tool)

| Tool | Description |
|------|-------------|
| `get_insights_with_breakdown` | Get insights broken down by age, gender, placement, device, etc. |

### Creative Assets (4 tools)

| Tool | Description |
|------|-------------|
| `upload_ad_image` | Upload an image (local path, URL, or Google Drive link) |
| `upload_ad_video` | Upload a video (local path, URL, or Google Drive link) |
| `get_video_upload_status` | Check video processing status |
| `get_ad_previews` | Generate ad preview URLs for different placements |

### Pixels & Custom Conversions (5 tools)

| Tool | Description |
|------|-------------|
| `get_pixels` | List pixels in an ad account |
| `create_pixel` | Create a new pixel |
| `get_pixel_stats` | Get pixel event stats for a time range |
| `get_custom_conversions` | List custom conversions in an ad account |
| `create_custom_conversion` | Create a custom conversion from a pixel event |

### Product Catalog & Commerce (9 tools)

| Tool | Description |
|------|-------------|
| `get_product_catalogs` | List product catalogs |
| `create_product_catalog` | Create a new product catalog |
| `get_product_sets` | List product sets in a catalog |
| `create_product_set` | Create a product set with filters |
| `get_products` | List products in a catalog |
| `create_product` | Add a product to a catalog |
| `update_product` | Update a product |
| `delete_product` | Remove a product from a catalog |
| `batch_upload_products` | Upload multiple products at once |

### Product Feeds (4 tools)

| Tool | Description |
|------|-------------|
| `get_product_feeds` | List feeds in a catalog |
| `create_product_feed` | Create a product feed (scheduled or manual) |
| `update_product_feed` | Update a product feed |
| `delete_product_feed` | Delete a product feed |

### Targeting Search (5 tools)

| Tool | Description |
|------|-------------|
| `search_targeting_interests` | Search interest targeting options |
| `search_geo_locations` | Search countries, cities, regions |
| `search_targeting_behaviors` | Search behavior targeting options |
| `search_targeting_demographics` | Search demographic targeting options |
| `browse_targeting_categories` | Browse all targeting categories |

### Advantage+ (4 tools)

| Tool | Description |
|------|-------------|
| `create_advantage_plus_shopping_campaign` | Create an Advantage+ Shopping campaign |
| `create_adset_with_advantage_audience` | Create an ad set using Advantage+ audience |
| `enable_advantage_creative` | Enable Advantage+ creative enhancements on a creative |
| `get_performance_recommendations` | Get AI-powered performance recommendations |

### Budget Schedules / Dayparting (3 tools)

| Tool | Description |
|------|-------------|
| `get_budget_schedules` | List budget schedules for a campaign |
| `create_budget_schedule` | Create a budget schedule (absolute or multiplier) |
| `delete_budget_schedule` | Delete a budget schedule |

### Split Tests / A/B Testing (3 tools)

| Tool | Description |
|------|-------------|
| `get_split_tests` | List split tests in an ad account |
| `create_split_test` | Create an A/B split test across campaigns |
| `get_split_test` | Get split test results and winner |

### Page (1 tool)

| Tool | Description |
|------|-------------|
| `get_page_posts` | Get posts from a Facebook Page |

---

## Architecture

```
server_sdk.py   — entry point, 57 tools (SDK + write operations)
server.py       — 21 read-only tools (raw Graph API)
```

`server_sdk.py` imports the FastMCP instance from `server.py`, so all 78 tools are registered on a single MCP instance. Claude Desktop only needs to run `server_sdk.py`.

---

## Dependencies

- [mcp](https://pypi.org/project/mcp/) >= 1.6.0
- [requests](https://pypi.org/project/requests/) >= 2.32.3
- [facebook-python-business-sdk](https://pypi.org/project/facebook-python-business-sdk/) >= 20.0.0

---

## License

MIT License — based on the original [gomarble-ai/facebook-ads-mcp-server](https://github.com/gomarble-ai/facebook-ads-mcp-server), extended with 57 additional tools.
