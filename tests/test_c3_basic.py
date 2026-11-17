"""C3 basic layout + standard Midea C3 SET for sn8 171H120F (Rotenso Aquami, #124).

Every frame below is a real capture from 2026-11-17 (EU cloud). Each SET was
sent once with the owner's approval, verified by re-query and reverted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.water_heater import STATE_HEAT_PUMP
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF
from homeassistant.exceptions import HomeAssistantError

from custom_components.iletcomfort.api import (
    ApiError,
    ILetComfortClient,
    ITSSensors,
    extract_c3_body,
    parse_hex_response,
)
from custom_components.iletcomfort.c3_basic import (
    BODY_ECO,
    BODY_SILENCE,
    apply_basic_changes,
    build_basic_set,
    build_c3_query,
    build_feature_set,
    decode_c3_basic,
)
from custom_components.iletcomfort.climate import ILetComfortC3Climate
from custom_components.iletcomfort.model_profiles import (
    ATW_SN8,
    apply_profile_to_sensors,
    ModelProfile,
    atw_c3_basic,
    decode_atw_status,
)
from custom_components.iletcomfort.switch import (
    ILetComfortC3CurveSwitch,
    ILetComfortC3FeatureSwitch,
)
from custom_components.iletcomfort.water_heater import ILetComfortDhw


def _body(csv_frame: str) -> bytes:
    """Body (incl. body-type byte) of a comma-separated cloud response."""
    return bytes(extract_c3_body(parse_hex_response(csv_frame))[1])


# Baseline: Zone-1 and DHW off, curve on, Zone-1 29 °C, DHW 55 °C, tank 16 °C.
ROTENSO_BASELINE = (
    "aa,23,c3,00,00,00,00,00,00,03,01,08,15,a0,03,03,1d,1e,37,30,37,19,19,05,"
    "37,19,19,05,3c,22,3c,14,10,00,80,97"
)
# SET responses (message type 0x02) — the device echoes its new full status.
ROTENSO_DHW_54 = (
    "aa,23,c3,00,00,00,00,00,00,02,01,08,15,a0,03,03,1d,1e,36,30,37,19,19,05,"
    "37,19,19,05,3c,22,3c,14,10,00,80,99"
)
ROTENSO_CURVE_OFF = (
    "aa,23,c3,00,00,00,00,00,00,02,01,00,15,a0,03,03,1d,1e,37,30,37,19,19,05,"
    "37,19,19,05,3c,22,3c,14,10,00,80,a0"
)
ROTENSO_DHW_ON = (
    "aa,23,c3,00,00,00,00,00,00,02,01,0c,15,a0,03,03,1d,1e,37,30,37,19,19,05,"
    "37,19,19,05,3c,22,3c,14,10,00,80,94"
)
# Issue #124: Zone-1 heating ON while the old decode showed "Off".
ROTENSO_ISSUE124_HEATING = bytes.fromhex(
    "01 0d 15 90 03 03 28 1e 32 30 41 23 19 05 37 19 19 05 3c 22 3c 14 32 00 80"
)
ROTENSO_SILENCE_QUERY = bytes.fromhex("05 00 0c 00 0f 00 16 00 07 00")
ROTENSO_ECO_QUERY = bytes.fromhex("07 00 08 00 13 00")
ROTENSO_SENSORS_PLACEHOLDER = bytes.fromhex(
    "02 00" + " 03 1e 00 00 00 00" * 6
)

# The exact SET frames the hardware accepted.
SET_DHW_54 = "aa12c3000000000000020100031d1e36300183"
SET_RESTORE = "aa12c3000000000000020100031d1e37300182"
SET_CURVE_OFF = "aa12c3000000000000020100031d1e37300083"
SET_ZONE1_28 = "aa12c3000000000000020100031c1e37300183"
SET_DHW_ON = "aa12c3000000000000020104031d1e3730017e"
SET_SILENT_ON = "aa14c30000000000000205010c000f0016000700e9"
SET_SILENT_OFF = "aa14c30000000000000205000c000f0016000700ea"
SET_ECO_ON = "aa11c3000000000000020701080013000007"
SET_ECO_OFF = "aa11c3000000000000020700080013000008"


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------

def test_decode_rotenso_baseline():
    basic = decode_c3_basic(_body(ROTENSO_BASELINE))
    assert basic is not None
    assert (basic.zone1_power, basic.dhw_power, basic.zone1_curve) == (False, False, True)
    assert basic.zone1_water_temp
    assert basic.zone1_setpoint == 29.0
    assert basic.zone1_setpoint_range == (25.0, 55.0)
    assert basic.dhw_target == 55
    assert (basic.dhw_min, basic.dhw_max) == (20, 60)
    assert basic.dhw_tank_temp == 16
    assert basic.room_target == 24.0
    assert not basic.silent_mode and not basic.eco_mode
    assert basic.error_code == 0


def test_decode_set_responses_track_the_change():
    assert decode_c3_basic(_body(ROTENSO_DHW_54)).dhw_target == 54
    assert decode_c3_basic(_body(ROTENSO_CURVE_OFF)).zone1_curve is False
    assert decode_c3_basic(_body(ROTENSO_DHW_ON)).dhw_power is True


def test_silent_and_eco_bits_in_status_byte3():
    body = bytearray(_body(ROTENSO_BASELINE))
    body[3] = 0xA2
    assert decode_c3_basic(body).silent_mode
    body[3] = 0xA8
    assert decode_c3_basic(body).eco_mode


@pytest.mark.parametrize(
    "body",
    [b"", b"\x01" * 10, bytes([0x02]) + bytes(30), bytes([0x01, 0, 0, 0, 7]) + bytes(20)],
)
def test_decode_rejects_non_basic_frames(body):
    assert decode_c3_basic(body) is None


def test_issue124_heating_shows_heat_not_off():
    """The #124 bug: heating on in the app, HA showed Off."""
    status = decode_atw_status(ROTENSO_ISSUE124_HEATING)
    assert status.mode_name == "Heat"
    assert status.t5s_def == 40.0
    assert status.c3_basic.zone1_power


def test_atw_status_uses_c3_basic_for_rotenso():
    status = decode_atw_status(_body(ROTENSO_BASELINE))
    assert status.mode_name == "Off"
    assert status.t5s_def == 29.0  # old byte[9]/2 mapping gave room target 24
    assert status.set_temperature == 55
    assert status.box_bottom_temp == 16.0
    assert status.comp_running is None


def test_rotenso_short_sensor_template_is_suppressed():
    status = decode_atw_status(_body(ROTENSO_BASELINE))
    sensors = ITSSensors(
        t4_temp=-32.0, twin_temp=-35.0, raw_body=ROTENSO_SENSORS_PLACEHOLDER,
    )
    out = apply_profile_to_sensors(ModelProfile.ATW, sensors, status)
    assert out.t4_temp is None and out.twin_temp is None
    assert out.th_temp == 16.0


def test_atw_c3_basic_is_gated_on_sn8():
    status = decode_atw_status(_body(ROTENSO_BASELINE))
    assert atw_c3_basic(ATW_SN8, status) is not None
    assert atw_c3_basic(None, status) is None
    assert atw_c3_basic("171000AU", status) is None


# ---------------------------------------------------------------------------
# SET frames — must be byte-identical to what the hardware accepted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"dhw_target": 54}, SET_DHW_54),
        ({"dhw_target": 55}, SET_RESTORE),
        ({"zone1_curve": False}, SET_CURVE_OFF),
        ({"zone1_setpoint": 28}, SET_ZONE1_28),
        ({"dhw_power": True}, SET_DHW_ON),
        ({}, SET_RESTORE),
    ],
)
def test_basic_set_matches_hardware_validated_frames(changes, expected):
    basic = decode_c3_basic(_body(ROTENSO_BASELINE))
    assert build_basic_set(apply_basic_changes(basic, **changes)) == expected


def test_zone1_power_sets_only_bit0():
    basic = decode_c3_basic(_body(ROTENSO_BASELINE))
    frame = bytes.fromhex(build_basic_set(apply_basic_changes(basic, zone1_power=True)))
    assert frame[11] == 0x01
    assert frame[12:18] == bytes.fromhex(SET_RESTORE)[12:18]


def test_room_type_setpoint_writes_room_target():
    body = bytearray(_body(ROTENSO_BASELINE))
    body[2] &= ~0x10  # room-temperature control
    basic = decode_c3_basic(body)
    assert basic.zone1_setpoint == 24.0 and basic.zone1_setpoint_step == 0.5
    frame = bytes.fromhex(build_basic_set(apply_basic_changes(basic, zone1_setpoint=22.5)))
    assert frame[16] == 45 and frame[13] == 0x1D


@pytest.mark.parametrize(
    "changes", [{"dhw_target": 61}, {"dhw_target": 19}, {"zone1_setpoint": 56}]
)
def test_out_of_range_is_rejected(changes):
    basic = decode_c3_basic(_body(ROTENSO_BASELINE))
    with pytest.raises(ValueError):
        apply_basic_changes(basic, **changes)


@pytest.mark.parametrize(
    ("query", "on", "expected"),
    [
        (ROTENSO_SILENCE_QUERY, True, SET_SILENT_ON),
        (ROTENSO_SILENCE_QUERY, False, SET_SILENT_OFF),
        (ROTENSO_ECO_QUERY, True, SET_ECO_ON),
        (ROTENSO_ECO_QUERY, False, SET_ECO_OFF),
    ],
)
def test_feature_set_keeps_schedule(query, on, expected):
    assert build_feature_set(query, on) == expected


def test_feature_set_rejects_other_bodies():
    with pytest.raises(ValueError):
        build_feature_set(_body(ROTENSO_BASELINE), True)


def test_query_frame_matches_existing_builder():
    from custom_components.iletcomfort.api import build_c3_query as legacy

    for body_type in (0x01, 0x02, BODY_SILENCE, BODY_ECO):
        assert build_c3_query(body_type) == legacy(body_type)


# ---------------------------------------------------------------------------
# Client write path
# ---------------------------------------------------------------------------

def test_client_set_c3_basic_reads_then_writes_one_change():
    client = ILetComfortClient()
    client.send_hex_command = MagicMock(side_effect=[ROTENSO_BASELINE, ROTENSO_DHW_54])
    result = client.set_c3_basic("APPL1", dhw_target=54)
    sent = [c.args[1] for c in client.send_hex_command.call_args_list]
    assert sent == [build_c3_query(0x01), SET_DHW_54]
    assert result["sent"] == SET_DHW_54


def test_client_set_c3_basic_refuses_unknown_layout():
    client = ILetComfortClient()
    client.send_hex_command = MagicMock(
        return_value="aa,10,c3,00,00,00,00,00,00,03,01,08,15,a0,03,07,1d,1e"
    )
    with pytest.raises(ApiError):
        client.set_c3_basic("APPL1", dhw_target=54)
    assert client.send_hex_command.call_count == 1


def test_client_set_c3_feature_copies_schedule():
    client = ILetComfortClient()
    client.send_hex_command = MagicMock(
        side_effect=["aa,14,c3,00,00,00,00,00,00,03,05,00,0c,00,0f,00,16,00,07,00,e9",
                     "aa,0b,c3,00,00,00,00,00,00,02,05,2b"]
    )
    client.set_c3_feature("APPL1", BODY_SILENCE, True)
    assert client.send_hex_command.call_args_list[1].args[1] == SET_SILENT_ON


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def _coordinator(frame: str = ROTENSO_BASELINE, sn8: str | None = ATW_SN8):
    coordinator = MagicMock()
    coordinator.appliance_code = "APPL1"
    coordinator.sn8 = sn8
    coordinator.data = {
        "status": decode_atw_status(_body(frame)),
        "sensors": ITSSensors(raw_body=ROTENSO_SENSORS_PLACEHOLDER),
    }
    coordinator.async_set_c3_basic = AsyncMock()
    coordinator.async_set_c3_feature = AsyncMock()
    return coordinator


async def test_climate_reports_zone1_state_and_limits():
    entity = ILetComfortC3Climate(_coordinator())
    assert entity.hvac_mode is HVACMode.OFF
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    assert entity.target_temperature == 29.0
    assert (entity.min_temp, entity.max_temp) == (25.0, 55.0)
    assert entity.current_temperature is None
    assert entity.extra_state_attributes["weather_curve"] is True

    heating = _coordinator()
    heating.data["status"] = decode_atw_status(ROTENSO_ISSUE124_HEATING)
    assert ILetComfortC3Climate(heating).hvac_mode is HVACMode.HEAT


async def test_climate_writes():
    coordinator = _coordinator()
    entity = ILetComfortC3Climate(coordinator)
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    coordinator.async_set_c3_basic.assert_awaited_with(zone1_power=True)
    await entity.async_turn_off()
    coordinator.async_set_c3_basic.assert_awaited_with(zone1_power=False)
    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 99})
    coordinator.async_set_c3_basic.assert_awaited_with(zone1_setpoint=55.0)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_hvac_mode(HVACMode.COOL)


async def test_water_heater_state_and_writes():
    coordinator = _coordinator()
    entity = ILetComfortDhw(coordinator)
    assert entity.current_operation == STATE_OFF
    assert entity.current_temperature == 16.0
    assert entity.target_temperature == 55.0
    assert (entity.min_temp, entity.max_temp) == (20.0, 60.0)
    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 54})
    coordinator.async_set_c3_basic.assert_awaited_with(dhw_target=54)
    await entity.async_set_operation_mode(STATE_HEAT_PUMP)
    coordinator.async_set_c3_basic.assert_awaited_with(dhw_power=True)
    await entity.async_turn_off()
    coordinator.async_set_c3_basic.assert_awaited_with(dhw_power=False)

    on = ILetComfortDhw(_coordinator(ROTENSO_DHW_ON))
    assert on.current_operation == STATE_HEAT_PUMP


async def test_switches():
    coordinator = _coordinator(ROTENSO_CURVE_OFF)
    curve = ILetComfortC3CurveSwitch(coordinator)
    assert curve.is_on is False
    await curve.async_turn_on()
    coordinator.async_set_c3_basic.assert_awaited_with(zone1_curve=True)

    silent = ILetComfortC3FeatureSwitch(
        coordinator, "silent_mode", "Silent Mode", "mdi:volume-off",
        BODY_SILENCE, lambda b: b.silent_mode,
    )
    assert silent.is_on is False
    await silent.async_turn_on()
    coordinator.async_set_c3_feature.assert_awaited_with(BODY_SILENCE, True)
