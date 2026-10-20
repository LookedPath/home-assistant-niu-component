"""Common device metadata for NIU entities."""

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class NiuCoordinatorEntity(CoordinatorEntity):
    """Attach each entity to its scooter's stable serial-based device."""

    @property
    def device_info(self):
        """Return known scooter metadata without inventing a firmware version."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.metadata.sn)},
            "name": self.coordinator.metadata.sensor_prefix,
            "manufacturer": "NIU",
            "model": "Electric Scooter",
        }
