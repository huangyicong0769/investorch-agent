from datetime import datetime
from zoneinfo import ZoneInfo

from agents.decorators import tool


@tool
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """
    Get the current local date and time.

    Args:
        timezone (str): The IANA timezone to use for the current time. Default is "Asia/Shanghai".

    Returns:
        str: The current local date and time as a string.
    """
    return datetime.now(ZoneInfo(timezone)).isoformat()