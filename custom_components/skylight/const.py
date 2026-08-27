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

# Update intervals (seconds) — defaults, overridable per entry via the options
# flow.
CALENDAR_SCAN_INTERVAL = 300
LISTS_SCAN_INTERVAL = 120
SENSOR_SCAN_INTERVAL = 300
FRAME_SCAN_INTERVAL = 600
PHOTOS_SCAN_INTERVAL = 900

# Options-flow keys → default seconds. This single mapping drives the options
# schema, the stored defaults and the coordinator wiring, so adding a poller
# means touching one place.
CONF_CALENDAR_INTERVAL = "calendar_scan_interval"
CONF_LISTS_INTERVAL = "lists_scan_interval"
CONF_SENSOR_INTERVAL = "sensor_scan_interval"
CONF_FRAME_INTERVAL = "frame_scan_interval"
CONF_PHOTOS_INTERVAL = "photos_scan_interval"

SCAN_INTERVAL_OPTIONS: dict[str, int] = {
    CONF_CALENDAR_INTERVAL: CALENDAR_SCAN_INTERVAL,
    CONF_LISTS_INTERVAL: LISTS_SCAN_INTERVAL,
    CONF_SENSOR_INTERVAL: SENSOR_SCAN_INTERVAL,
    CONF_FRAME_INTERVAL: FRAME_SCAN_INTERVAL,
    CONF_PHOTOS_INTERVAL: PHOTOS_SCAN_INTERVAL,
}

# Skylight is a cloud API with no published rate limit; 30s is already far
# faster than a photo frame needs and keeps a misconfiguration from hammering it.
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 7200

# Platforms
PLATFORM_CALENDAR = "calendar"
PLATFORM_TODO = "todo"
PLATFORM_SENSOR = "sensor"
PLATFORM_IMAGE = "image"
PLATFORM_SWITCH = "switch"
PLATFORM_NUMBER = "number"
PLATFORM_TIME = "time"
PLATFORM_BINARY_SENSOR = "binary_sensor"

# Chore status literals.
#
# Skylight spells a finished chore "complete". Note that list_items use
# "completed" — the two resources genuinely differ, so don't unify them.
CHORE_STATUS_COMPLETE = "complete"
CHORE_STATUS_PENDING = "pending"
# Statuses that count as done when *reading* the feed. Both spellings are
# accepted so an API-version change can't silently un-tick every chore in HA.
CHORE_COMPLETE_STATUSES = frozenset({"complete", "completed"})

# Meal category names (well-known IDs from Skylight production).
# Used to break out per-slot meal sensors even when no meal is planned.
MEAL_CATEGORY_NAMES = {
    "9115870": "Breakfast",
    "9115871": "Lunch",
    "9115872": "Dinner",
    "9115873": "Snack",
}
