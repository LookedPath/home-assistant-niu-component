"""Compatibility with entity identities used by upstream NIU releases."""

from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


BATTERY_SUFFIXES = {
    "battery_charge_a": "battery_charge",
    "battery_grade_a": "battery_grade",
}


def migrate_battery_entities(hass, entry, serial: str) -> None:
    """Keep entity IDs and history when adopting canonical battery-A names.

    An existing canonical entity wins a collision; never overwrite its registry
    entry or remove an entity that may still be referenced by an automation.
    """
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain != "sensor" or entity.platform != DOMAIN:
            continue
        for old_suffix, new_suffix in BATTERY_SUFFIXES.items():
            if entity.unique_id != f"sensor.niu_scooter_{serial}_{old_suffix}":
                continue
            unique_id = f"sensor.niu_scooter_{serial}_{new_suffix}"
            if registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None:
                registry.async_update_entity(entity.entity_id, new_unique_id=unique_id)
