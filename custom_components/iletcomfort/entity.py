"""Shared entity helpers for the iLetComfort integration."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import ILetComfortCoordinator


def build_device_info(coordinator: ILetComfortCoordinator) -> DeviceInfo:
    """Build the DeviceInfo that groups every entity under one HA Device.

    Keyed on the stable appliance code so all platforms attach to the same
    device. ``sw_version`` is taken from the outdoor-unit firmware when the
    coordinator already has sensor data (it does by the time platforms are set
    up, since the first refresh runs during ``async_setup_entry``).
    """
    sensors = (coordinator.data or {}).get("sensors")
    sw_version = getattr(sensors, "odu_version", "") or None
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.appliance_code)},
        name=coordinator.entry.title,
        manufacturer="iLetComfort",
        model=coordinator.sn8,
        sw_version=sw_version,
    )


def remove_stale_entities(
    hass: HomeAssistant,
    coordinator: ILetComfortCoordinator,
    platform: str,
    keys: Iterable[str],
) -> None:
    """Remove registry entries this appliance's layout no longer creates.

    Used for C3 basic-layout units (sn8 171H120F), whose cloud data does not
    carry most legacy readings: dropping them avoids permanent "unavailable"
    or fabricated-zero entities left over from earlier versions.
    """
    registry = er.async_get(hass)
    for key in keys:
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"{coordinator.appliance_code}_{key}"
        )
        if entity_id is not None:
            registry.async_remove(entity_id)
