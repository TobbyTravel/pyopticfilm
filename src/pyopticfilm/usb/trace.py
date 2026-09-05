# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured USB transactions and a recording transport wrapper."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pyopticfilm.usb.protocol import UsbTransport

UsbOp = Literal["control_read", "control_write", "bulk_read", "bulk_write"]

UsbTxnListener = Callable[["UsbTransaction"], None]


@dataclass
class UsbTransaction:
    """One USB transfer as seen at the :class:`UsbTransport` boundary."""

    operation: UsbOp
    request_type: int | None = None
    request: int | None = None
    value: int | None = None
    index: int | None = None
    endpoint: int | None = None
    data: bytes | None = None
    length: int | None = None
    timeout_ms: int | None = None
    # Host-side monotonic timestamps (seconds, time.perf_counter()), not device
    # time. t0 is set right before the transfer is issued, t1 right after it
    # returns (or raises) - so t1-t0 is the observed round-trip latency for
    # this one transfer, and consecutive t0s give inter-command spacing. Not
    # populated for entries built via from_json() (recorded pcap data has its
    # own timestamps instead).
    t0: float | None = None
    t1: float | None = None
    response: bytes | None = None
    error: str | None = None
    # Correlation IDs (usb/correlate.py), stamped by RecordingTransport from
    # the ambient context at record time - not set by callers directly.
    investigation_id: str | None = None
    experiment_id: str | None = None
    run_id: str | None = None
    scan_id: str | None = None
    operation_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.operation}
        if self.t0 is not None:
            out["t0"] = self.t0
        if self.t1 is not None:
            out["t1"] = self.t1
            out["latency_ms"] = round((self.t1 - (self.t0 or self.t1)) * 1000, 3)
        if self.response is not None:
            out["response"] = self.response.hex()
        if self.error is not None:
            out["error"] = self.error
        for corr_field in (
            "investigation_id",
            "experiment_id",
            "run_id",
            "scan_id",
            "operation_id",
        ):
            corr_val = getattr(self, corr_field)
            if corr_val is not None:
                out[corr_field] = corr_val
        if self.request_type is not None:
            out["request_type"] = self.request_type
        if self.request is not None:
            out["request"] = self.request
        if self.value is not None:
            out["value"] = self.value
        if self.index is not None:
            out["index"] = self.index
        if self.endpoint is not None:
            out["endpoint"] = self.endpoint
        if self.data is not None:
            out["data"] = self.data.hex()
        if self.length is not None:
            out["length"] = self.length
        if self.timeout_ms is not None:
            out["timeout_ms"] = self.timeout_ms
        return out

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> UsbTransaction:
        data_hex = obj.get("data")
        data = bytes.fromhex(data_hex) if isinstance(data_hex, str) else None
        return cls(
            operation=obj["op"],
            request_type=obj.get("request_type"),
            request=obj.get("request"),
            value=obj.get("value"),
            index=obj.get("index"),
            endpoint=obj.get("endpoint"),
            data=data,
            length=obj.get("length"),
            timeout_ms=obj.get("timeout_ms"),
        )


def format_usb_log_line(txn: UsbTransaction, *, max_data: int = 24) -> str:
    """One-line USB log entry (payloads truncated)."""
    if txn.operation == "bulk_read":
        return f"bulk_read  ep=0x{int(txn.endpoint or 0):02x} length={txn.length}"
    if txn.operation == "bulk_write":
        payload = txn.data or b""
        shown = payload[:max_data].hex()
        extra = f"…(+{len(payload) - max_data}b)" if len(payload) > max_data else ""
        return f"bulk_write ep=0x{int(txn.endpoint or 0):02x} {len(payload)}b {shown}{extra}"
    bits = [
        txn.operation,
        f"type=0x{int(txn.request_type or 0):02x}",
        f"req=0x{int(txn.request or 0):02x}",
        f"val=0x{int(txn.value or 0):04x}",
        f"idx=0x{int(txn.index or 0):04x}",
    ]
    if txn.length is not None:
        bits.append(f"len={txn.length}")
    if txn.data:
        shown = txn.data[:max_data].hex()
        extra = f"…(+{len(txn.data) - max_data}b)" if len(txn.data) > max_data else ""
        bits.append(f"data={shown}{extra}")
    return " ".join(bits)


@dataclass
class RecordingTransport:
    """Wrap a :class:`UsbTransport` and record every transfer in order.

    ``keep_in_memory=False`` drops each :class:`UsbTransaction` after it is
    handed to ``sink``/``listener`` instead of accumulating it in
    ``transactions`` - needed for multi-hundred-MB/GB live sessions (a full
    high-DPI image pass is many thousands of bulk_in transactions) where
    holding every one in RAM defeats the point of a streaming sink. Default
    is ``True`` (matches prior behavior) since most Scan Lab commands so far
    are short, register-only sessions where in-memory is simplest.
    """

    inner: UsbTransport
    listener: UsbTxnListener | None = None
    sink: Callable[[UsbTransaction], None] | None = None
    keep_in_memory: bool = True
    transactions: list[UsbTransaction] = field(default_factory=list)

    def __post_init__(self) -> None:
        from pyopticfilm.usb.correlate import current

        self._current_correlation = current

    def _record(self, txn: UsbTransaction) -> None:
        corr = self._current_correlation()
        txn.investigation_id = corr.investigation_id
        txn.experiment_id = corr.experiment_id
        txn.run_id = corr.run_id
        txn.scan_id = corr.scan_id
        txn.operation_id = corr.operation_id
        if self.keep_in_memory:
            self.transactions.append(txn)
        if self.sink is not None:
            self.sink(txn)
        if self.listener is not None:
            self.listener(txn)

    def control_msg(
        self,
        request_type: int,
        request: int,
        value: int,
        index: int,
        data_or_length: int | bytes | bytearray,
        *,
        timeout_ms: int | None = None,
    ) -> bytes:
        is_read = isinstance(data_or_length, int)
        if is_read:
            txn = UsbTransaction(
                operation="control_read",
                request_type=int(request_type),
                request=int(request),
                value=int(value),
                index=int(index),
                length=int(data_or_length),
                timeout_ms=timeout_ms,
            )
        else:
            txn = UsbTransaction(
                operation="control_write",
                request_type=int(request_type),
                request=int(request),
                value=int(value),
                index=int(index),
                data=bytes(data_or_length),
                timeout_ms=timeout_ms,
            )
        txn.t0 = time.perf_counter()
        try:
            result = self.inner.control_msg(
                request_type,
                request,
                value,
                index,
                data_or_length,
                timeout_ms=timeout_ms,
            )
        except Exception as exc:
            txn.t1 = time.perf_counter()
            txn.error = f"{type(exc).__name__}: {exc}"
            self._record(txn)
            raise
        txn.t1 = time.perf_counter()
        if is_read:
            txn.response = bytes(result)
        self._record(txn)
        return result

    def bulk_read(self, size: int, *, timeout_ms: int | None = None) -> bytes:
        txn = UsbTransaction(
            operation="bulk_read",
            endpoint=0x81,
            length=int(size),
            timeout_ms=timeout_ms,
        )
        txn.t0 = time.perf_counter()
        try:
            data = self.inner.bulk_read(size, timeout_ms=timeout_ms)
        except Exception as exc:
            txn.t1 = time.perf_counter()
            txn.error = f"{type(exc).__name__}: {exc}"
            self._record(txn)
            raise
        txn.t1 = time.perf_counter()
        self._record(txn)
        return data

    def bulk_write(self, data: bytes | bytearray, *, timeout_ms: int | None = None) -> int:
        payload = bytes(data)
        txn = UsbTransaction(
            operation="bulk_write",
            endpoint=0x02,
            data=payload,
            timeout_ms=timeout_ms,
        )
        txn.t0 = time.perf_counter()
        try:
            written = self.inner.bulk_write(payload, timeout_ms=timeout_ms)
        except Exception as exc:
            txn.t1 = time.perf_counter()
            txn.error = f"{type(exc).__name__}: {exc}"
            self._record(txn)
            raise
        txn.t1 = time.perf_counter()
        self._record(txn)
        return written

    def abort_bulk_in(self, *args: Any, **kwargs: Any) -> int:
        abort = getattr(self.inner, "abort_bulk_in", None)
        if not callable(abort):
            return 0
        return int(abort(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)
