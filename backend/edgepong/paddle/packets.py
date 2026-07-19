"""Binary wire protocol shared with the ESP32 paddle firmware.

All packets are little-endian, versioned, and carry a trailing CRC32 computed
over every preceding byte. Layouts mirror ``paddle-firmware/include/packets.h``.

Keeping encode/decode here (and unit tested) means the exact same byte layout is
enforced on both ends of the UDP link.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

MAGIC = 0x5047  # "PG"
VERSION = 1

MSG_TELEMETRY = 1
MSG_HAPTIC = 2
MSG_LED = 3
MSG_HEARTBEAT = 4

# ---- status_bits (telemetry) ---------------------------------------------- #
STATUS_IMU_CALIBRATED = 1 << 0
STATUS_WIFI_CONNECTED = 1 << 1
STATUS_LOW_BATTERY = 1 << 2
STATUS_HAPTIC_LIMITED = 1 << 3
STATUS_SENSOR_FAULT = 1 << 4

# ---- haptic flags --------------------------------------------------------- #
HAPTIC_FLAG_FLASH_RGB = 1 << 0
HAPTIC_FLAG_PERFECT = 1 << 1
HAPTIC_FLAG_ERROR = 1 << 2
HAPTIC_FLAG_CANCEL = 1 << 3

# ---- waveforms ------------------------------------------------------------ #
WAVE_PULSE = 0
WAVE_RAMP = 1
WAVE_DOUBLE_TAP = 2


class PacketError(ValueError):
    """Raised on magic / version / CRC / length validation failure."""


def _crc32(payload: bytes) -> int:
    return zlib.crc32(payload) & 0xFFFFFFFF


# --------------------------------------------------------------------------- #
# Telemetry: ESP32 -> UNO Q   (message_type = 1)
# --------------------------------------------------------------------------- #
# H  magic, B version, B type, I sequence, Q paddle_time_us,
# 4f quat, 3f gyro, 3f accel, H battery_mv, B button_bits, B status_bits
_TELEMETRY_BODY = struct.Struct("<HBBIQ10fHBB")
_TELEMETRY_FULL = struct.Struct("<HBBIQ10fHBBI")  # body + crc32


@dataclass
class PaddleTelemetry:
    sequence: int
    paddle_time_us: int
    quat_w: float
    quat_x: float
    quat_y: float
    quat_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float
    accel_x: float
    accel_y: float
    accel_z: float
    battery_mv: int = 3900
    button_bits: int = 0
    status_bits: int = STATUS_IMU_CALIBRATED | STATUS_WIFI_CONNECTED

    def encode(self) -> bytes:
        body = _TELEMETRY_BODY.pack(
            MAGIC, VERSION, MSG_TELEMETRY,
            self.sequence & 0xFFFFFFFF, self.paddle_time_us & 0xFFFFFFFFFFFFFFFF,
            self.quat_w, self.quat_x, self.quat_y, self.quat_z,
            self.gyro_x, self.gyro_y, self.gyro_z,
            self.accel_x, self.accel_y, self.accel_z,
            self.battery_mv & 0xFFFF, self.button_bits & 0xFF, self.status_bits & 0xFF,
        )
        return body + struct.pack("<I", _crc32(body))

    @classmethod
    def decode(cls, data: bytes) -> "PaddleTelemetry":
        if len(data) != _TELEMETRY_FULL.size:
            raise PacketError(f"telemetry length {len(data)} != {_TELEMETRY_FULL.size}")
        body, crc = data[:-4], struct.unpack("<I", data[-4:])[0]
        if _crc32(body) != crc:
            raise PacketError("telemetry crc mismatch")
        vals = _TELEMETRY_BODY.unpack(body)
        magic, version, mtype = vals[0], vals[1], vals[2]
        if magic != MAGIC:
            raise PacketError(f"bad magic 0x{magic:04X}")
        if version != VERSION:
            raise PacketError(f"unsupported version {version}")
        if mtype != MSG_TELEMETRY:
            raise PacketError(f"not telemetry (type {mtype})")
        return cls(
            sequence=vals[3], paddle_time_us=vals[4],
            quat_w=vals[5], quat_x=vals[6], quat_y=vals[7], quat_z=vals[8],
            gyro_x=vals[9], gyro_y=vals[10], gyro_z=vals[11],
            accel_x=vals[12], accel_y=vals[13], accel_z=vals[14],
            battery_mv=vals[15], button_bits=vals[16], status_bits=vals[17],
        )


# --------------------------------------------------------------------------- #
# Haptic command: UNO Q -> ESP32   (message_type = 2)
# --------------------------------------------------------------------------- #
# H magic, B version, B type, I command_sequence, Q execute_time_us,
# 4B quadrant intensity, H duration_ms, B waveform, B flags
_HAPTIC_BODY = struct.Struct("<HBBIQ4BHBB")
_HAPTIC_FULL = struct.Struct("<HBBIQ4BHBBI")


@dataclass
class HapticCommand:
    command_sequence: int
    q0: int
    q1: int
    q2: int
    q3: int
    duration_ms: int
    waveform: int = WAVE_PULSE
    flags: int = 0
    execute_time_us: int = 0  # 0 = immediate

    def encode(self) -> bytes:
        body = _HAPTIC_BODY.pack(
            MAGIC, VERSION, MSG_HAPTIC,
            self.command_sequence & 0xFFFFFFFF, self.execute_time_us & 0xFFFFFFFFFFFFFFFF,
            self.q0 & 0xFF, self.q1 & 0xFF, self.q2 & 0xFF, self.q3 & 0xFF,
            self.duration_ms & 0xFFFF, self.waveform & 0xFF, self.flags & 0xFF,
        )
        return body + struct.pack("<I", _crc32(body))

    @classmethod
    def decode(cls, data: bytes) -> "HapticCommand":
        if len(data) != _HAPTIC_FULL.size:
            raise PacketError(f"haptic length {len(data)} != {_HAPTIC_FULL.size}")
        body, crc = data[:-4], struct.unpack("<I", data[-4:])[0]
        if _crc32(body) != crc:
            raise PacketError("haptic crc mismatch")
        vals = _HAPTIC_BODY.unpack(body)
        if vals[0] != MAGIC:
            raise PacketError(f"bad magic 0x{vals[0]:04X}")
        if vals[1] != VERSION:
            raise PacketError(f"unsupported version {vals[1]}")
        if vals[2] != MSG_HAPTIC:
            raise PacketError(f"not haptic (type {vals[2]})")
        return cls(
            command_sequence=vals[3], execute_time_us=vals[4],
            q0=vals[5], q1=vals[6], q2=vals[7], q3=vals[8],
            duration_ms=vals[9], waveform=vals[10], flags=vals[11],
        )


# --------------------------------------------------------------------------- #
# LED command: UNO Q -> ESP32   (message_type = 3)
# --------------------------------------------------------------------------- #
_LED_BODY = struct.Struct("<HBBI3BBHH")
_LED_FULL = struct.Struct("<HBBI3BBHHI")

LED_SOLID = 0
LED_FLASH = 1
LED_PULSE = 2
LED_BLINK = 3


@dataclass
class LedCommand:
    sequence: int
    red: int
    green: int
    blue: int
    effect: int = LED_FLASH
    duration_ms: int = 120
    reserved: int = 0

    def encode(self) -> bytes:
        body = _LED_BODY.pack(
            MAGIC, VERSION, MSG_LED, self.sequence & 0xFFFFFFFF,
            self.red & 0xFF, self.green & 0xFF, self.blue & 0xFF,
            self.effect & 0xFF, self.duration_ms & 0xFFFF, self.reserved & 0xFFFF,
        )
        return body + struct.pack("<I", _crc32(body))

    @classmethod
    def decode(cls, data: bytes) -> "LedCommand":
        if len(data) != _LED_FULL.size:
            raise PacketError(f"led length {len(data)} != {_LED_FULL.size}")
        body, crc = data[:-4], struct.unpack("<I", data[-4:])[0]
        if _crc32(body) != crc:
            raise PacketError("led crc mismatch")
        vals = _LED_BODY.unpack(body)
        if vals[0] != MAGIC or vals[1] != VERSION or vals[2] != MSG_LED:
            raise PacketError("bad led header")
        return cls(
            sequence=vals[3], red=vals[4], green=vals[5], blue=vals[6],
            effect=vals[7], duration_ms=vals[8], reserved=vals[9],
        )


def peek_message_type(data: bytes) -> int | None:
    """Return the message_type byte without full validation (for routing)."""
    if len(data) < 4:
        return None
    magic = struct.unpack_from("<H", data, 0)[0]
    if magic != MAGIC:
        return None
    return data[3]
