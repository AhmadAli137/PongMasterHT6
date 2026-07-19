"""Round-trip and CRC validation for the binary wire protocol (spec §24.1)."""

import struct

import pytest

from edgepong.paddle.packets import (
    HapticCommand,
    LedCommand,
    PacketError,
    PaddleTelemetry,
    peek_message_type,
    MSG_TELEMETRY,
    MSG_HAPTIC,
)


def _sample_telemetry() -> PaddleTelemetry:
    return PaddleTelemetry(
        sequence=12345,
        paddle_time_us=9_876_543_210,
        quat_w=1.0, quat_x=0.0, quat_y=0.0, quat_z=0.0,
        gyro_x=0.1, gyro_y=-0.2, gyro_z=0.3,
        accel_x=0.0, accel_y=9.81, accel_z=0.0,
        battery_mv=3900, button_bits=1, status_bits=0b11,
    )


def test_telemetry_round_trip():
    tel = _sample_telemetry()
    decoded = PaddleTelemetry.decode(tel.encode())
    assert decoded.sequence == tel.sequence
    assert decoded.paddle_time_us == tel.paddle_time_us
    assert decoded.battery_mv == 3900
    assert decoded.button_bits == 1
    assert pytest.approx(decoded.accel_y, rel=1e-6) == 9.81


def test_telemetry_crc_detects_corruption():
    data = bytearray(_sample_telemetry().encode())
    data[10] ^= 0xFF  # flip a payload byte
    with pytest.raises(PacketError):
        PaddleTelemetry.decode(bytes(data))


def test_telemetry_bad_magic():
    data = bytearray(_sample_telemetry().encode())
    struct.pack_into("<H", data, 0, 0x0000)
    # recompute crc so only magic is wrong
    import zlib
    body = bytes(data[:-4])
    data[-4:] = struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    with pytest.raises(PacketError):
        PaddleTelemetry.decode(bytes(data))


def test_haptic_round_trip():
    cmd = HapticCommand(command_sequence=7, q0=255, q1=0, q2=128, q3=64, duration_ms=40)
    decoded = HapticCommand.decode(cmd.encode())
    assert (decoded.q0, decoded.q1, decoded.q2, decoded.q3) == (255, 0, 128, 64)
    assert decoded.duration_ms == 40
    assert decoded.command_sequence == 7


def test_led_round_trip():
    cmd = LedCommand(sequence=3, red=10, green=200, blue=30, duration_ms=120)
    decoded = LedCommand.decode(cmd.encode())
    assert (decoded.red, decoded.green, decoded.blue) == (10, 200, 30)


def test_peek_message_type():
    assert peek_message_type(_sample_telemetry().encode()) == MSG_TELEMETRY
    assert peek_message_type(HapticCommand(1, 0, 0, 0, 0, 10).encode()) == MSG_HAPTIC
    assert peek_message_type(b"\x00\x01") is None


def test_wrong_length_rejected():
    with pytest.raises(PacketError):
        PaddleTelemetry.decode(b"\x47\x50\x01\x01")
