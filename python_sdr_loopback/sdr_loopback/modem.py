from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .packet import Packet, PacketError, HEADER
from .rrc import root_raised_cosine_taps


PREAMBLE_BYTES = bytes.fromhex("1a cf fc 1d c5 72 0f e3")


@dataclass(frozen=True)
class ModemConfig:
    sample_rate: float = 1_000_000.0
    symbol_rate: float = 250_000.0
    rrc_alpha: float = 0.35
    rrc_span_symbols: int = 8
    tx_amplitude: float = 0.25

    @property
    def samples_per_symbol(self) -> int:
        ratio = self.sample_rate / self.symbol_rate
        rounded = int(round(ratio))
        if abs(ratio - rounded) > 1e-9 or rounded <= 0:
            raise ValueError("sample_rate must be an integer multiple of symbol_rate")
        return rounded


class QpskLoopbackModem:
    def __init__(self, config: ModemConfig = ModemConfig()) -> None:
        self.config = config
        self.sps = config.samples_per_symbol
        self.rrc = root_raised_cosine_taps(self.sps, config.rrc_span_symbols, config.rrc_alpha)
        self.filter_delay = (len(self.rrc) - 1) // 2
        self.preamble_symbols = self._bytes_to_symbols(PREAMBLE_BYTES)

    def transmit(self, payload: bytes, sequence: int = 0) -> np.ndarray:
        packet = Packet(sequence=sequence, payload=payload).encode()
        packet_symbols = self._bytes_to_symbols(packet)
        symbols = np.concatenate([self.preamble_symbols, packet_symbols])
        upsampled = np.zeros(len(symbols) * self.sps, dtype=np.complex64)
        upsampled[:: self.sps] = symbols.astype(np.complex64)
        shaped = np.convolve(upsampled, self.rrc, mode="full")
        shaped *= self.config.tx_amplitude / max(np.max(np.abs(shaped)), 1e-12)
        return shaped.astype(np.complex64)

    def receive(self, iq: np.ndarray) -> Packet:
        if iq.ndim != 1:
            raise ValueError("iq must be a 1-D complex array")

        matched = np.convolve(iq.astype(np.complex64), self.rrc, mode="full")
        best = self._find_preamble(matched)
        if best is None:
            raise PacketError("preamble not found")

        start, gain = best
        header_symbols = HEADER.size * 4
        header_bytes = self._symbols_to_bytes(self._sample_symbols(matched, start, header_symbols), gain)
        if len(header_bytes) != HEADER.size:
            raise PacketError("header decode failed")

        try:
            _magic, _version, _flags, _sequence, payload_len = HEADER.unpack(header_bytes)
        except Exception as exc:
            raise PacketError("header unpack failed") from exc

        total_bytes = HEADER.size + int(payload_len) + 4
        if total_bytes <= HEADER.size + 4 or total_bytes > 65535:
            raise PacketError("invalid payload length")

        total_symbols = total_bytes * 4
        raw_symbols = self._sample_symbols(matched, start, total_symbols)
        decoded = self._symbols_to_bytes(raw_symbols, gain)
        return Packet.decode(decoded)

    def _find_preamble(self, matched: np.ndarray) -> tuple[int, complex] | None:
        best_score = 0.0
        best_start = 0
        best_gain = 1.0 + 0.0j
        search_limit = len(matched) - len(self.preamble_symbols) * self.sps
        if search_limit <= 0:
            return None

        for start in range(0, search_limit):
            samples = self._sample_symbols(matched, start, len(self.preamble_symbols))
            gain = np.vdot(self.preamble_symbols, samples) / np.vdot(self.preamble_symbols, self.preamble_symbols)
            aligned = samples / (gain if abs(gain) > 1e-12 else 1.0)
            error = np.mean(np.abs(aligned - self.preamble_symbols) ** 2)
            score = 1.0 / (error + 1e-9)
            if score > best_score:
                best_score = score
                best_start = start
                best_gain = gain

        if best_score < 5.0:
            return None
        return best_start + len(self.preamble_symbols) * self.sps, best_gain

    def _sample_symbols(self, samples: np.ndarray, start: int, count: int) -> np.ndarray:
        indexes = start + np.arange(count) * self.sps
        if indexes[-1] >= len(samples):
            raise PacketError("not enough IQ samples")
        return samples[indexes]

    @staticmethod
    def _bytes_to_symbols(data: bytes) -> np.ndarray:
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
        dibits = bits.reshape(-1, 2)
        i = np.where(dibits[:, 0] == 0, 1.0, -1.0)
        q = np.where(dibits[:, 1] == 0, 1.0, -1.0)
        return ((i + 1j * q) / np.sqrt(2.0)).astype(np.complex64)

    @staticmethod
    def _symbols_to_bytes(symbols: np.ndarray, gain: complex) -> bytes:
        equalized = symbols / (gain if abs(gain) > 1e-12 else 1.0)
        bits = np.empty(len(equalized) * 2, dtype=np.uint8)
        bits[0::2] = np.real(equalized) < 0
        bits[1::2] = np.imag(equalized) < 0
        return np.packbits(bits).tobytes()
