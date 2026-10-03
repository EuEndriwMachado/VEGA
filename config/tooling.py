import os

from config.environment import load_vega_environment


load_vega_environment()

TOOL_TIMEOUT_SECONDS = float(os.getenv("VEGA_TOOL_TIMEOUT", "10"))
TOOL_USER_AGENT = os.getenv("VEGA_USER_AGENT", "VegaToolClient/1.0")
TOOL_REQUESTS_PER_MINUTE = int(os.getenv("VEGA_TOOL_REQUESTS_PER_MINUTE", "30"))
