"""Switch entities for the iLetComfort integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .c3_basic import BODY_ECO, BODY_SILENCE, C3BasicStatus
from .const import DOMAIN
from .coordinator import ILetComfortCoordinator
from .entity import build_device_info, remove_stale_entities
from .model_profiles import atw_c3_basic


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    coordinator: ILetComfortCoordinator = hass.data[DOMAIN][entry.entry_id]
    status = (coordinator.data or {}).get("status")
    if atw_c3_basic(coordinator.sn8, status) is not None:
        # The legacy Boost write/state do not apply to the C3 basic layout.
        remove_stale_entities(hass, coordinator, "switch", ["boost"])
        async_add_entities(
            [
                ILetComfortC3CurveSwitch(coordinator),
                ILetComfortC3FeatureSwitch(
                    coordinator, "silent_mode", "Silent Mode", "mdi:volume-off",
                    BODY_SILENCE, lambda b: b.silent_mode,
                ),
                ILetComfortC3FeatureSwitch(
                    coordinator, "eco_mode", "ECO Mode", "mdi:leaf",
                    BODY_ECO, lambda b: b.eco_mode,
                ),
            ]
        )
        return
    async_add_entities([ILetComfortBoostSwitch(coordinator)])


class ILetComfortBoostSwitch(CoordinatorEntity[ILetComfortCoordinator], SwitchEntity):
    """Switch entity for boost mode."""

    _attr_has_entity_name = True
    _attr_name = "Boost"
    _attr_icon = "mdi:rocket-launch"

    def __init__(self, coordinator: ILetComfortCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.appliance_code}_boost"
        self._attr_device_info = build_device_info(coordinator)

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        sensors = self.coordinator.data.get("sensors")
        if sensors is None:
            return False
        return sensors.ctrl_flag == 2

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device(boost=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device(boost=False)


class _ILetComfortC3Switch(CoordinatorEntity[ILetComfortCoordinator], SwitchEntity):
    """Base for switches backed by the C3 basic status (sn8 171H120F)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ILetComfortCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.appliance_code}_{key}"
        self._attr_device_info = build_device_info(coordinator)

    @property
    def _basic(self) -> C3BasicStatus | None:
        status = (self.coordinator.data or {}).get("status")
        return atw_c3_basic(self.coordinator.sn8, status)

    @property
    def available(self) -> bool:
        return super().available and self._basic is not None


class ILetComfortC3CurveSwitch(_ILetComfortC3Switch):
    """Zone-1 weather-compensation curve on/off."""

    _attr_name = "Weather Curve"
    _attr_icon = "mdi:chart-bell-curve-cumulative"

    def __init__(self, coordinator: ILetComfortCoordinator) -> None:
        super().__init__(coordinator, "zone1_curve")

    @property
    def is_on(self) -> bool | None:
        basic = self._basic
        return basic.zone1_curve if basic is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_c3_basic(zone1_curve=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_c3_basic(zone1_curve=False)


class ILetComfortC3FeatureSwitch(_ILetComfortC3Switch):
    """Silent / ECO mode, written with its own C3 SET body type."""

    def __init__(
        self,
        coordinator: ILetComfortCoordinator,
        key: str,
        name: str,
        icon: str,
        body_type: int,
        state_fn: Callable[[C3BasicStatus], bool],
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_name = name
        self._attr_icon = icon
        self._body_type = body_type
        self._state_fn = state_fn

    @property
    def is_on(self) -> bool | None:
        basic = self._basic
        return self._state_fn(basic) if basic is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_c3_feature(self._body_type, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_c3_feature(self._body_type, False)
