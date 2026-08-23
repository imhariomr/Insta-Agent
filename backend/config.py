"""Central config: paths, env vars, tunables. Everything else imports from here."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "agent-data")
DB_PATH = os.path.join(DATA_DIR, "agency.db")
BATCHES_DIR = os.path.join(DATA_DIR, "batches")


def _load_dotenv():
    """Tiny hand-rolled .env loader (KEY=VALUE per line) so secrets don't
    have to be set by hand in every terminal — no need for a new dependency
    just for this. Real environment variables always win over the file."""
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# The two existing local apps we reuse in-process (see plan doc). Override
# via env var if they ever move.
YT_APP_DIR = os.environ.get(
    "YT_APP_DIR", r"C:\Users\HP\Downloads\New folder (3)\New folder\yt"
)
EDITOR_APP_DIR = os.environ.get(
    "EDITOR_APP_DIR", r"C:\Users\HP\Downloads\New folder (3)\New folder\editor"
)
# The editor's own Flask app (imported in-process via external_apps) is
# started as a real server on this port only when a "Manual edit" is first
# requested — its own docs default to 5050, kept here so it never collides
# with this app's own port (5100) or the media server's (5101).
EDITOR_APP_PORT = int(os.environ.get("EDITOR_APP_PORT", "5050"))

# LLM: OpenAI-SDK-compatible client pointed at NVIDIA NIM.
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
NIM_BASE_URL = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
# meta/llama3-70b-instruct (this project's original choice) 404s on this
# NIM account now — deprecated/renamed on NVIDIA's catalog. Verified via
# client.models.list() that meta/llama-3.3-70b-instruct is actually
# available; override with NIM_TEXT_MODEL if your account differs.
NIM_TEXT_MODEL = os.environ.get("NIM_TEXT_MODEL", "meta/llama-3.3-70b-instruct")
# This vision model has no text-only chat mode issue, but frame-analysis
# calls still need a real VLM, which the text model above is not.
NIM_VISION_MODEL = os.environ.get("NIM_VISION_MODEL", "meta/llama-3.2-90b-vision-instruct")
# Some hosted VLMs (this one included) reject more than 1 image per request
# unless the deployment raised --limit-mm-per-prompt. Raise this if yours
# supports more.
NIM_VISION_MAX_IMAGES = int(os.environ.get("NIM_VISION_MAX_IMAGES", "1"))

# Instagram Graph API. Empty until the user configures them -> Sophia must
# report "not connected" rather than fake a publish.
IG_USER_ID = os.environ.get("IG_USER_ID", "")
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
# Meta has two parallel flows: "Instagram API with Facebook Login" (token
# prefix EAA..., calls go to graph.facebook.com, needs a Page-linked IG
# Business account) vs the newer "Instagram API with Instagram Login"
# (token prefix IGAA..., calls go to graph.instagram.com). Verified via a
# direct /me call that this account's IGAA... token only works against the
# instagram.com host — graph.facebook.com replies "Cannot parse access
# token" for it. Override IG_GRAPH_HOST if you're on the Facebook Login flow.
IG_GRAPH_HOST = os.environ.get("IG_GRAPH_HOST", "https://graph.instagram.com").rstrip("/")
IG_GRAPH_VERSION = os.environ.get("IG_GRAPH_VERSION", "v21.0")

# Graph API can only fetch media from a public HTTPS URL, never a local
# file. "auto" (default): backend/tunnel.py opens a Cloudflare Quick Tunnel
# to the minimal media server and discovers the URL itself — nothing to
# paste into .env. "fixed": use PUBLIC_BASE_URL below as-is (production,
# behind a real domain) and never start a tunnel.
PUBLIC_BASE_URL_MODE = os.environ.get("PUBLIC_BASE_URL_MODE", "auto").strip().lower()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
# Only the media server (health check + /media/<batch_id>/<file>) is ever
# tunneled — never the main app (chat/API/DB). Kept on its own port so a
# public tunnel can never reach the rest of the app.
MEDIA_SERVER_PORT = int(os.environ.get("MEDIA_SERVER_PORT", "5101"))
# Leave blank to auto-detect cloudflared on PATH (shutil.which already
# handles the .exe suffix on Windows); set explicitly if it's not on PATH.
CLOUDFLARED_PATH = os.environ.get("CLOUDFLARED_PATH", "").strip()

CLIP_DURATION_SECONDS = 29
MAX_QA_RETRY_LOOPS = 2
MAX_STAGE_RETRIES = 1
PIPELINE_WORKERS = 3

# Meta's own guidance for a media container's status_code: "query once per
# minute, for no more than 5 minutes." Applies to every container type
# (single video, and the parent CAROUSEL container) — Meta's docs don't
# differentiate polling cadence by media type.
IG_CONTAINER_POLL_INTERVAL_SECONDS = 60
IG_CONTAINER_POLL_TIMEOUT_SECONDS = 300
# Secondary safety net for Meta's "media not ready to publish yet" error
# (code 9007 / subcode 2207027), which has been observed even after a
# container reports FINISHED. Not the primary fix — see instagram_tool.py.
IG_PUBLISH_RETRY_COUNT = 4
IG_PUBLISH_RETRY_DELAY_SECONDS = 5

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BATCHES_DIR, exist_ok=True)


def batch_dir(batch_id):
    path = os.path.join(BATCHES_DIR, str(batch_id))
    for sub in ("downloaded", "captions", "processed", "qa", "final"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return path
