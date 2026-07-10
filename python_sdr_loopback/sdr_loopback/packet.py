from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass


MAGIC = b"SDR1"
VERSION = 1
HEADER = struct.Struct("<4sBBHI")


class PacketError(ValueError):
    """Raised when a packet cannot be parsed or fails CRC validation."""


@dataclass(frozen=True)
class Packet:
    sequence: int
    payload: bytes

    def encode(self) -> bytes:
        if not 0 <= self.sequence <= 0xFFFF:
            raise ValueError("sequence must fit in uint16")
        if len(self.payload) > 0xFFFF:
            raise ValueError("payload is too large")

        crc = zlib.crc32(self.payload) & 0xFFFFFFFF
        return HEADER.pack(MAGIC, VERSION, 0, self.sequence, len(self.payload)) + self.payload + struct.pack("<I", crc)

    @staticmethod
    def decode(data: bytes) -> "Packet":
        if len(data) < HEADER.size + 4:
            raise PacketError("packet too short")

        magic, version, _flags, sequence, payload_len = HEADER.unpack_from(data)
        if magic != MAGIC:
            raise PacketError("bad packet magic")
        if version != VERSION:
            raise PacketError(f"unsupported packet version {version}")

        expected_len = HEADER.size + payload_len + 4
        if len(data) < expected_len:
            raise PacketError("packet truncated")

        payload = data[HEADER.size : HEADER.size + payload_len]
        expected_crc = struct.unpack_from("<I", data, HEADER.size + payload_len)[0]
        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise PacketError("payload CRC mismatch")

        return Packet(sequence=sequence, payload=payload)
