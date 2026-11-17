"""Tests for shared device grouping and the ODU Current sensor (issue #10)."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.iletcomfort.api import ITSSensors, ITSStatus
from custom_components.iletcomfort.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    ILetComfortBinarySensor,
)
from custom_components.iletcomfort.climate import ILetComfortClimate
from custom_components.iletcomfort.const import (
    CONF_APPLIANCE_CODE,
    CONF_REGION,
    DOMAIN,
    REGION_US,
)
from custom_components.iletcomfort.coordinator import ILetComfortCoordinator
from custom_components.iletcomfort.error_codes import ERROR_CODES
from custom_components.iletcomfort.select import ILetComfortMuteSelect
from custom_components.iletcomfort.sensor import (
    SENSOR_DESCRIPTIONS,
    ILetComfortSensor,
)
from custom_components.iletcomfort.switch import ILetComfortBoostSwitch


def _coordinator(hass: HomeAssistant) -> ILetComfortCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pool Heat Pump",
        unique_id="user@example.com:APPL1",
        data={
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_APPLIANCE_CODE: "APPL1",
            CONF_REGION: REGION_US,
        },
        version=2,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.iletcomfort.coordinator.ILetComfortClient"):
        coord = ILetComfortCoordinator(hass, entry)
    coord.data = {
        "status": ITSStatus(mode=1),
        "sensors": ITSSensors(odu_current=4.0, odu_version="1.2.3"),
    }
    return coord


def test_all_platforms_share_one_device(hass: HomeAssistant):
    """Every platform's entity must attach to the same Device by appliance_code."""
    coord = _coordinator(hass)
    entities = [
        ILetComfortSensor(coord, SENSOR_DESCRIPTIONS[0]),
        ILetComfortBinarySensor(coord, BINARY_SENSOR_DESCRIPTIONS[0]),
        ILetComfortClimate(coord),
        ILetComfortBoostSwitch(coord),
        ILetComfortMuteSelect(coord),
    ]

    expected_identifiers = {(DOMAIN, "APPL1")}
    for ent in entities:
        assert ent.device_info is not None
        assert ent.device_info["identifiers"] == expected_identifiers


def test_device_info_uses_entry_title_and_firmware(hass: HomeAssistant):
    """Device page shows its name, model code, and ODU firmware."""
    coord = _coordinator(hass)
    coord.appliance_meta = {"sn8": "171H120F"}
    info = ILetComfortSensor(coord, SENSOR_DESCRIPTIONS[0]).device_info

    assert info is not None
    assert info.get("name") == "Pool Heat Pump"
    assert info.get("manufacturer") == "iLetComfort"
    assert info.get("model") == "171H120F"
    assert info.get("sw_version") == "1.2.3"


def test_odu_current_sensor_exists_and_reads_scaled_amps(hass: HomeAssistant):
    """The ODU Current sensor (issue #10/#11) must expose the scaled Ampere value.

    odu_current is decoded as fixed-point Amperes (raw / 256), so the sensor
    surfaces the physical value directly rather than the raw 16-bit count.
    """
    coord = _coordinator(hass)
    desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "odu_current")

    sensor = ILetComfortSensor(coord, desc)
    assert sensor.native_value == 4.0
    assert sensor.extra_state_attributes is None


def _error_code_sensor(hass: HomeAssistant, error_code: int) -> ILetComfortSensor:
    """Build the existing numeric error-code sensor with a selected code."""
    coord = _coordinator(hass)
    coord.data["status"].error_code = error_code
    desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "error_code")
    return ILetComfortSensor(coord, desc)


def test_known_error_code_adds_panel_code_and_description(hass: HomeAssistant):
    """Known faults retain their numeric state and expose vendor details."""
    assert len(ERROR_CODES) == 58
    sensor = _error_code_sensor(hass, 61)

    assert sensor.native_value == 61
    assert sensor.extra_state_attributes == {
        "panel": "E0",
        "description": "Water flow fault (after 3 consecutive flow faults)",
    }


def test_vendor_error_code_31_exposes_operating_range_fault(
    hass: HomeAssistant,
):
    """The additional vendor-plugin bA entry reported in issue #48 is mapped."""
    sensor = _error_code_sensor(hass, 31)

    assert sensor.native_value == 31
    assert sensor.extra_state_attributes == {
        "panel": "bA",
        "description": "Outdoor temperature exceeds the allowable operating range.",
    }


def test_unknown_error_code_has_no_fault_attributes(hass: HomeAssistant):
    """Unmapped faults pass through without making the sensor unavailable."""
    sensor = _error_code_sensor(hass, 255)

    assert sensor.native_value == 255
    assert sensor.extra_state_attributes == {}


def test_zero_error_code_has_no_fault_attributes(hass: HomeAssistant):
    """The no-fault value does not expose fault metadata."""
    sensor = _error_code_sensor(hass, 0)

    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {}


async def test_c3_basic_unit_only_gets_entities_it_can_read(hass: HomeAssistant):
    """sn8 171H120F (C3 basic layout): no fabricated/always-unknown entities,
    and stale ones from earlier versions are removed from the registry."""
    from homeassistant.helpers import entity_registry as er

    from custom_components.iletcomfort import (
        binary_sensor,
        climate,
        select,
        sensor,
        switch,
        water_heater,
    )
    from custom_components.iletcomfort.model_profiles import (
        ATW_SN8,
        decode_atw_status,
    )

    coord = _coordinator(hass)
    coord.appliance_meta = {"sn8": ATW_SN8}
    coord.data = {
        "status": decode_atw_status(bytes.fromhex(
            "01 08 15 a0 03 03 1d 1e 37 30 37 19 19 05 37 19 19 05 3c 22 3c 14 10 00 80"
        )),
        "sensors": ITSSensors(),
    }
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    registry = er.async_get(hass)
    for platform, key in [
        ("sensor", "total_energy"),
        ("binary_sensor", "compressor_running"),
        ("switch", "boost"),
        ("select", "mute"),
        ("sensor", "dhw_tank"),
    ]:
        registry.async_get_or_create(
            platform, DOMAIN, f"APPL1_{key}", config_entry=coord.entry,
        )

    created: dict[str, list] = {}
    for module in (sensor, binary_sensor, switch, select, climate, water_heater):
        name = module.__name__.rsplit(".", 1)[-1]
        await module.async_setup_entry(
            hass, coord.entry, lambda ents, name=name: created.setdefault(name, []).extend(ents),
        )

    keys = {name: sorted(e.unique_id.removeprefix("APPL1_") for e in ents)
            for name, ents in created.items()}
    assert keys == {
        "sensor": ["dhw_tank", "error_code"],
        "binary_sensor": ["error"],
        "switch": ["eco_mode", "silent_mode", "zone1_curve"],
        "climate": ["climate"],
        "water_heater": ["dhw"],
    }
    assert registry.async_get_entity_id("sensor", DOMAIN, "APPL1_total_energy") is None
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, "APPL1_compressor_running") is None
    assert registry.async_get_entity_id("switch", DOMAIN, "APPL1_boost") is None
    assert registry.async_get_entity_id("select", DOMAIN, "APPL1_mute") is None
    # Entities that are still created keep their registry entry (history).
    assert registry.async_get_entity_id("sensor", DOMAIN, "APPL1_dhw_tank") is not None
