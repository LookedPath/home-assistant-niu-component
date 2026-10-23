"""Privacy-preserving endpoint health diagnostics for NIU."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import STALE_AFTER_SECONDS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return health and known field types, never account or vehicle payloads."""
    coordinator = entry.runtime_data
    return {
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "endpoints": {
            name: {
                "last_success": health.last_success.isoformat()
                if health.last_success else None,
                "consecutive_failures": health.consecutive_failures,
                # Upstream descriptions can contain private data; omit them here.
                "has_error": health.error is not None,
                "error_type": health.error_type,
            }
            for name, health in coordinator.endpoint_health.items()
        },
        "battery_structure": coordinator.api.battery_structure,
    }
