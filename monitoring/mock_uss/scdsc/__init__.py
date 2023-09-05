import functools

from loguru import logger

from monitoring.mock_uss import require_config_value
from monitoring.mock_uss.config import KEY_DSS_URL, KEY_AUTH_SPEC
from monitoring.monitorlib import auth
from monitoring.monitorlib.infrastructure import UTMClientSession

from monitoring.mock_uss import webapp

require_config_value(KEY_DSS_URL)
require_config_value(KEY_AUTH_SPEC)

utm_client = UTMClientSession(
    webapp.config[KEY_DSS_URL],
    auth.make_auth_adapter(webapp.config[KEY_AUTH_SPEC]),
)
