"""Constants for the Skylight Calendar integration."""

DOMAIN = "skylight"

# Config entry keys
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_DEVICE_FINGERPRINT = "device_fingerprint"
CONF_FRAME_ID = "frame_id"
CONF_FRAME_NAME = "frame_name"

# API
BASE_URL = "https://app.ourskylight.com"
OAUTH_URL = "https://app.ourskylight.com/oauth/token"
OAUTH_AUTHORIZE_URL = "https://app.ourskylight.com/oauth/authorize"
OAUTH_REDIRECT_URI = "https://ourskylight.com/welcome"
OAUTH_CLIENT_ID = "skylight-mobile"
OAUTH_SCOPE = "everything"
API_VERSION = "2026-05-01"
CLIENT_ID = "skylight-mobile"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Update intervals (seconds)
CALENDAR_SCAN_INTERVAL = 300
LISTS_SCAN_INTERVAL = 120
SENSOR_SCAN_INTERVAL = 300
FRAME_SCAN_INTERVAL = 600
PHOTOS_SCAN_INTERVAL = 900

# Platforms
PLATFORM_CALENDAR = "calendar"
PLATFORM_TODO = "todo"
PLATFORM_SENSOR = "sensor"
PLATFORM_IMAGE = "image"
PLATFORM_SWITCH = "switch"
PLATFORM_NUMBER = "number"

# Meal category names (well-known IDs from Skylight production).
# Used to break out per-slot meal sensors even when no meal is planned.
MEAL_CATEGORY_NAMES = {
    "9115870": "Breakfast",
    "9115871": "Lunch",
    "9115872": "Dinner",
    "9115873": "Snack",
}
