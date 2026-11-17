"""Water heater (DHW) entity for C3 basic-layout units (sn8 171H120F)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_HEAT_PUMP,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ILetComfortCoordinator
from .entity import build_device_info
from .model_profiles import atw_c3_basic


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the DHW entity only for validated C3 basic-layout units."""
    coordinator: ILetComfortCoordinator = hass.data[DOMAIN][entry.entry_id]
    status = (coordinator.data or {}).get("status")
    basic = atw_c3_basic(coordinator.sn8, status)
    if basic is not None and basic.cap_dhw:
        async_add_entities([ILetComfortDhw(coordinator)])


class ILetComfortDhw(CoordinatorEntity[ILetComfortCoordinator], WaterHeaterEntity):
    """Domestic hot water: on/off and target temperature."""

    _attr_has_entity_name = True
    _attr_name = "Hot Water"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_operation_list = [STATE_OFF, STATE_HEAT_PUMP]
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.ON_OFF
        | WaterHeaterEntityFeature.OPERATION_MODE
    )

    def __init__(self, coordinator: ILetComfortCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.appliance_code}_dhw"
        self._attr_device_info = build_device_info(coordinator)

    @property
    def _basic(self):
        status = (self.coordinator.data or {}).get("status")
        return atw_c3_basic(self.coordinator.sn8, status)

    @property
    def available(self) -> bool:
        return super().available and self._basic is not None

    @property
    def current_operation(self) -> str | None:
        basic = self._basic
        if basic is None:
            return None
        return STATE_HEAT_PUMP if basic.dhw_power else STATE_OFF

    @property
    def current_temperature(self) -> float | None:
        basic = self._basic
        return float(basic.dhw_tank_temp) if basic is not None else None

    @property
    def target_temperature(self) -> float | None:
        basic = self._basic
        return float(basic.dhw_target) if basic is not None else None

    @property
    def min_temp(self) -> float:
        basic = self._basic
        return float(basic.dhw_min) if basic is not None else 20.0

    @property
    def max_temp(self) -> float:
        basic = self._basic
        return float(basic.dhw_max) if basic is not None else 60.0

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        clamped = max(self.min_temp, min(float(temp), self.max_temp))
        await self.coordinator.async_set_c3_basic(dhw_target=int(round(clamped)))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode not in self._attr_operation_list:
            raise HomeAssistantError(f"Unsupported operation mode {operation_mode}")
        await self.coordinator.async_set_c3_basic(
            dhw_power=operation_mode == STATE_HEAT_PUMP,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_c3_basic(dhw_power=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_c3_basic(dhw_power=False)
