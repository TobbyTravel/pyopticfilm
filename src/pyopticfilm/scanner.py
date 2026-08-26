# SPDX-License-Identifier: GPL-3.0-or-later
"""High-level scanner façade."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Self

import numpy as np

from pyopticfilm.advanced import AdvancedRegisters
from pyopticfilm.asic.status import ScannerStatus
from pyopticfilm.device.protocol import ScanMethod
from pyopticfilm.device.select import (
    FilmModel,
    create_asic,
    model_for_device,
    model_is_scan_ready,
)
from pyopticfilm.exceptions import AsicError, PlustekError, ScanCancelled, ScanError
from pyopticfilm.image import ScanImage
from pyopticfilm.logging import get_logger
from pyopticfilm.scan.calibrate import CalibEntry, Calibrator, default_cache_path
from pyopticfilm.scan.exposure_calibrate import (
    CalibStepStats,
    ExposurePlan,
    format_step_table,
    pick_high,
    pick_short,
    step_stats,
)
from pyopticfilm.scan.session import ScanSession
from pyopticfilm.usb.device import UsbDeviceHandle
from pyopticfilm.usb.fake import FakeDeviceHandle, MockScannerTransport
from pyopticfilm.usb.protocol import GenesysUsbProtocol, UsbTransport

logger = get_logger(__name__)

# GL128 prime (discarded first image pass) overrides. By default the Scanner
# reuses the model's small ME-calibration geometry (see
# :meth:`Scanner._gl128_priming_geometry`). Override with ``POF_GL128_PRIME``:
#   unset/empty   -> model's calibration geometry (small safe pass)
#   full          -> full pass at the requested PPI and area (legacy)
#   <dpi>:x0,y0,x1,y1  -> custom pass, e.g. 600:0.0,0.0,1.0,0.12
_PRIME_ENV = "POF_GL128_PRIME"


ScanMode = Literal["color", "infrared", "gray"]


class Scanner:
    """User-facing entry point for OpticFilm scanners (hardware-tested GL128)."""

    def __init__(
        self,
        handle: UsbDeviceHandle,
        protocol: GenesysUsbProtocol | None = None,
        asic: Any | None = None,
        *,
        model: FilmModel | None = None,
        calib_cache: Path | None = None,
    ) -> None:
        self._handle = handle
        self._protocol = protocol or GenesysUsbProtocol(handle)
        self._model = model or model_for_device(
            handle.info.product_id,
            getattr(handle.info, "bcd_device", 0),
        )
        self._asic = asic or create_asic(self._protocol, self._model)
        self._advanced = AdvancedRegisters(self._protocol)
        self._calibrator = Calibrator(
            self._asic,
            cache_path=calib_cache if calib_cache is not None else default_cache_path(),
            model=self._model,  # type: ignore[arg-type]
        )
        self._closed = False
        self._last_me_debug = None
        #: Lab / session may disarm GL128 briefly for stationary shading.
        self._bringup_motor_armed = bool(model_is_scan_ready(self._model))
        #: When True, scan/home/park are allowed even if ``scan_ready`` is False.
        #: Set only by :meth:`open_fake` (mock USB). Real hardware stays gated.
        self._allow_unvalidated_scan = False
        #: GL128's first image pass after open establishes the repeatable AGOHOME
        #: park position. It is discarded before the first user-visible scan.
        self._gl128_primed = False
        #: Most recent :meth:`calibrate_exposure` result (``None`` until run).
        self._last_exposure_calibration: ExposurePlan | None = None

    def _gl128_priming_geometry(self) -> tuple[int, tuple[float, float, float, float] | None]:
        """(resolution, area) for the discarded GL128 prime pass.

        The prime only needs a *small, valid* image pass — AGOHOME parks the
        carriage to the same repeatable home regardless of PPI or crop — so it
        reuses the model's ME calibration geometry instead of the requested
        scan's. The small central crop is probe-validated; larger prime
        geometries have failed with ``ValueError: Invalid scan area`` on the
        8100 V2. Override with :data:`_PRIME_ENV`; a resolution of ``0`` means
        "use the requested values" (the legacy full pass).
        """
        raw = os.environ.get(_PRIME_ENV, "").strip()
        if not raw:
            model_area = getattr(self._model, "me_calib_area", (0.4, 0.4, 0.6, 0.6))
            return (
                int(getattr(self._model, "me_calib_resolution", 1200)),
                (float(model_area[0]), float(model_area[1]), float(model_area[2]), float(model_area[3])),
            )
        if raw.lower() == "full":
            return 0, None
        if ":" in raw:
            dpi_part, area_part = raw.split(":", 1)
            x0, y0, x1, y1 = (float(v) for v in area_part.split(","))
            return int(dpi_part), (x0, y0, x1, y1)
        raise ValueError(f"POF_GL128_PRIME must be 'full' or '<dpi>:x0,y0,x1,y1', got {raw!r}")

    @classmethod
    def open(
        cls,
        device_id: str | None = None,
        *,
        calib_cache: Path | None = None,
    ) -> Self:
        """Open a scan-ready device when present, else the first matching OpticFilm."""
        handle = UsbDeviceHandle.open(device_id)
        scanner = cls(handle, calib_cache=calib_cache)
        logger.info(
            "Scanner open: %s model=%s asic=%s scan_ready=%s",
            handle.info.device_id,
            scanner._model.name,
            scanner._model.asic,
            model_is_scan_ready(scanner._model),
        )
        return scanner

    @classmethod
    def open_fake(
        cls,
        model: FilmModel,
        transport: UsbTransport | None = None,
        *,
        calib_cache: Path | None = None,
    ) -> Self:
        """Open ``model`` against a mock USB device (no hardware, no ``scan_ready``).

        Does not change ``model.scan_ready``. Real :meth:`open` stays gated.
        """
        inner = transport if transport is not None else MockScannerTransport()
        handle = FakeDeviceHandle.for_model(model)
        protocol = GenesysUsbProtocol(inner)
        scanner = cls(handle, protocol, model=model, calib_cache=calib_cache)
        scanner._allow_unvalidated_scan = True
        scanner._bringup_motor_armed = True
        if hasattr(scanner._asic, "_motor_moves_enabled"):
            scanner._asic._motor_moves_enabled = True
        logger.info(
            "Scanner open_fake: model=%s asic=%s (mock USB)",
            scanner._model.name,
            scanner._model.asic,
        )
        return scanner

    def close(self) -> None:
        if not self._closed:
            try:
                if self._asic._initialized:
                    self._asic.lamp_off()
                    self._asic.stop_motor()
            except Exception as exc:  # noqa: BLE001
                logger.debug("close cleanup: %s", exc)
            self._handle.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def device_id(self) -> str:
        return self._handle.info.device_id

    @property
    def model(self) -> FilmModel:
        return self._model

    @property
    def protocol(self) -> GenesysUsbProtocol:
        self._ensure_open()
        return self._protocol

    @property
    def asic(self) -> Any:
        self._ensure_open()
        return self._asic

    @property
    def advanced(self) -> AdvancedRegisters:
        """Low-level register access (debug / bring-up only)."""
        self._ensure_open()
        return self._advanced

    @property
    def calibrator(self) -> Calibrator:
        self._ensure_open()
        return self._calibrator

    def status(self) -> ScannerStatus:
        self._ensure_open()
        return self._asic.read_status_reliable()

    def warmup(self, *, home: bool = True, lamp: bool = True) -> None:
        """ASIC boot, frontend init, optional home + lamp on."""
        self._ensure_scan_ready()
        self._ensure_open()
        self._asic.init()
        if home:
            self._asic.home()
        if lamp:
            self._asic.set_scan_method("transparency")
            self._asic.lamp_on()

    def lamp_on(self, method: ScanMethod = "transparency") -> None:
        """Turn the lamp on. Allowed on probe-only models that implement lamp."""
        self._ensure_open()
        self._asic.set_scan_method(method)
        self._asic.lamp_on()

    def lamp_off(self) -> None:
        self._ensure_open()
        self._asic.lamp_off()

    def home(self, *, timeout_s: float = 30.0) -> None:
        self._ensure_scan_ready()
        self._ensure_open()
        self._asic.home(timeout_s=timeout_s)

    def park(self, *, timeout_s: float = 30.0) -> None:
        self._ensure_scan_ready()
        self._ensure_open()
        self._asic.park(timeout_s=timeout_s)

    def calibrate(
        self,
        *,
        resolution: int = 1800,
        mode: ScanMode = "color",
        force: bool = False,
        area: tuple[float, float, float, float] | None = None,
        geometry: object | None = None,
    ) -> CalibEntry:
        """Run dark/white shading (or IR white-only) and update the cache.

        GL128 AFE/ASIC shading runs with the motor disarmed, then re-arms so
        the following image feed can move.
        """
        self._ensure_scan_ready()
        self._ensure_open()
        if not self._asic._initialized:
            self._asic.init()
            if not self._asic.is_at_home():
                self._asic.home()
        was_armed = self._bringup_motor_armed
        try:
            if getattr(self._model, "asic", "") == "GL128":
                self.disarm_bringup_motor()
            return self._calibrator.run(
                resolution=resolution,
                mode=mode,
                force=force,
                area=area,
                geometry=geometry,  # type: ignore[arg-type]
            )
        finally:
            if was_armed:
                self.arm_bringup_motor()

    @property
    def last_me_debug(self):
        """Bracket planes / IVW stats from the last ME scan (GL128 only), or ``None``."""
        return self._last_me_debug

    @property
    def last_exposure_calibration(self) -> ExposurePlan | None:
        """Most recent :meth:`calibrate_exposure` result, or ``None`` until run.

        When this is set, a subsequent ``scan(..., multi_exposure=True)``
        without explicit ``me_exposures`` uses the plan's ``exposures``.
        """
        return self._last_exposure_calibration

    def calibrate_exposure(
        self,
        *,
        resolution: int | None = None,
        area: tuple[float, float, float, float] | None = None,
        progress: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> ExposurePlan:
        """Measure the loaded film and choose the two ME exposures.

        Runs one image pass per exposure candidate through the same linear
        per-pass path the multi-exposure scan uses (no film-base makeup,
        no IVW merge), then applies the pure selection functions in
        :mod:`pyopticfilm.scan.exposure_calibrate`:

        * short = lowest candidate with usable density contrast
          (``p90/p05 >= me_calib_min_short_contrast``);
        * long  = highest candidate with ``clip_92_max <=
          me_calib_clip_threshold``.

        If no low candidate is usable the model's existing ``exposure_short``
        is used as a safe fallback. The resulting
        :class:`~pyopticfilm.scan.exposure_calibrate.ExposurePlan` is cached
        on :attr:`last_exposure_calibration` and its two-element
        ``exposures`` tuple is what a later ``scan(..., multi_exposure=True)``
        consumes when no explicit ``me_exposures`` is given.

        Calibration geometry defaults to the model's small central crop
        (``me_calib_resolution`` / ``me_calib_area``); pass ``resolution`` /
        ``area`` to override. A GL128 prime pass is run automatically if
        this is the first scan of the session so the AGOHOME park is
        established before the first useful pass (same mechanism as
        :meth:`scan`).
        """
        self._ensure_scan_ready()
        self._ensure_open()
        if not self._asic._initialized:
            self._asic.init()
            if not self._asic.is_at_home():
                self._asic.home()

        resolution = int(resolution or getattr(self._model, "me_calib_resolution", 1200))
        raw_area = area if area is not None else getattr(self._model, "me_calib_area", (0.4, 0.4, 0.6, 0.6))
        if len(raw_area) != 4:
            raise ValueError(f"calibrate_exposure: area must have 4 values, got {raw_area!r}")
        area = (float(raw_area[0]), float(raw_area[1]), float(raw_area[2]), float(raw_area[3]))
        if not (0.0 <= area[0] < area[2] <= 1.0 and 0.0 <= area[1] < area[3] <= 1.0) or resolution <= 0:
            raise ValueError(f"calibrate_exposure: bad area={area} resolution={resolution}")

        short_cands = tuple(int(e) for e in getattr(self._model, "exposure_short_candidates", ()))
        long_cands = tuple(int(e) for e in getattr(self._model, "exposure_long_candidates", ()))
        if not short_cands or not long_cands:
            raise ScanError(
                f"{self._model.model} ({self._model.asic}) declares no "
                "exposure_short_candidates / exposure_long_candidates — "
                "adaptive exposure calibration is not available for this model."
            )
        clip_threshold = float(getattr(self._model, "me_calib_clip_threshold", 0.005))
        min_contrast = float(getattr(self._model, "me_calib_min_short_contrast", 3.0))

        # Ensure the GL128 AGOHOME park is established before the first real pass.
        if getattr(self._model, "asic", "") == "GL128" and not self._gl128_primed:
            prime_dpi, prime_area = self._gl128_priming_geometry()
            if prime_area is None:
                prime_area = area
            if prime_dpi == 0:
                prime_dpi = resolution
            self._gl128_prime_pass(
                resolution=prime_dpi,
                area=prime_area,
            )

        from pyopticfilm.scan.geometry import compute_geometry
        from pyopticfilm.scan.session import create_session

        geometry = compute_geometry(resolution, model=self._model, area=area)
        all_cands = sorted(set(short_cands) | set(long_cands))
        logger.info(
            "Exposure calibration: %d candidates %s @%ddpi area=%s clip<=%.3f%%",
            len(all_cands),
            all_cands,
            resolution,
            area,
            100.0 * clip_threshold,
        )

        steps: list[CalibStepStats] = []
        for i, exposure in enumerate(all_cands):
            session: ScanSession = create_session(self._asic, self._model, self._calibrator)
            session._pass_exposure = int(exposure)
            logger.info("Exposure calib [%d/%d]: scanning @exposure=%d", i + 1, len(all_cands), exposure)
            if progress is not None:
                progress(i / len(all_cands))
            try:
                raw = session.acquire_raw(
                    geometry,
                    method="transparency",
                    lamp_on=True,
                    start_motor=True,
                    cancel=cancel,
                )
            finally:
                session._pass_exposure = None
            calib = self._calibrator.find_for_scan(
                method="transparency", geometry=geometry
            )
            use_host = (
                calib is not None and self._calibrator.should_apply_host_calib()
            )
            dark = calib.dark if use_host else None
            white = calib.white if use_host else None
            plane = session.pipeline.assemble(
                raw,
                geometry,
                dark=dark,
                white=white,
                planar=bool(getattr(self._model, "usb_planar_rgb", False)),
                expose_base=False,  # keep the plane linear — that is the point
            )
            step = step_stats(
                int(exposure),
                np.asarray(plane, dtype=np.uint16),
                clip_threshold=clip_threshold,
            )
            steps.append(step)
            logger.info(
                "  exposure=%d mean=%.0f p05=%.0f p90=%.0f p99=%.0f clip92_max=%.3f%% n=%.2f %s",
                step.exposure,
                step.mean_dn,
                step.p05,
                step.p90,
                step.p99,
                100.0 * step.clip_92_max,
                step.noise_ratio,
                "usable" if step.usable else "REJECTED",
            )
            if cancel is not None and cancel.is_set():
                raise ScanCancelled("cancelled during exposure calibration")

        # Pick short: lowest usable short candidate; model default as safe fallback.
        short_picked, short_reasons = pick_short(
            steps,
            candidates=short_cands,
            min_contrast=min_contrast,
        )
        model_short = int(getattr(self._model, "exposure_short", self._model.exposure_lperiod))
        if short_picked is None:
            logger.warning(
                "No short candidate usable; falling back to model default %d:\n%s",
                model_short,
                "\n".join(short_reasons),
            )
            short = model_short
        else:
            short = short_picked
        # Pick long: highest clip-clean candidate; safe minimum fallback.
        long = pick_high(
            steps,
            candidates=long_cands,
            clip_threshold=clip_threshold,
        )

        plan = ExposurePlan(
            short=int(short),
            long=int(long),
            exposures=(int(short), int(long)),
            steps=tuple(steps),
            source=f"exposure_calibrate:{self._model.model}",
            area=tuple(area),
            resolution=int(resolution),
        )
        self._last_exposure_calibration = plan
        if progress is not None:
            progress(1.0)
        logger.info(
            "Exposure plan: short=%d long=%d exposures=%s\n%s",
            plan.short,
            plan.long,
            plan.exposures,
            format_step_table(plan.steps),
        )
        return plan

    def _gl128_prime_pass(
        self,
        *,
        resolution: int,
        area: tuple[float, float, float, float] | None,
    ) -> None:
        """Run the single discarded GL128 prime pass (no progress/cancel/ME/IR)."""
        from pyopticfilm.scan.session import create_session

        logger.info(
            "GL128 priming pass (%s dpi, area=%s): discard first image to establish AGOHOME park",
            resolution,
            area,
        )
        prime_session = create_session(self._asic, self._model, self._calibrator)
        prime_session.run(
            resolution=resolution,
            area=area,
            progress=None,
            cancel=None,
            apply_calib=False,
            multi_exposure=False,
            infrared=False,
        )
        self._gl128_primed = True

    def scan(
        self,
        *,
        resolution: int = 1800,
        mode: ScanMode = "color",
        area: tuple[float, float, float, float] | None = None,
        geometry: object | None = None,
        progress: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
        apply_calib: bool = True,
        multi_exposure: bool = False,
        infrared: bool = False,
        align_passes: bool = True,
        me_exposures: tuple[int, ...] | None = None,
    ) -> ScanImage:
        self._ensure_scan_ready()
        self._ensure_open()
        if not self._asic._initialized:
            self._asic.init()
            if not self._asic.is_at_home():
                self._asic.home()
        from pyopticfilm.scan.session import create_session

        if multi_exposure and me_exposures is None and self._last_exposure_calibration is not None:
            me_exposures = self._last_exposure_calibration.exposures
        if multi_exposure and me_exposures is not None:
            logger.info(
                "scan multi_exposure=True using exposures=%s",
                tuple(int(e) for e in me_exposures),
            )
        run_kwargs = {
            "resolution": resolution,
            "mode": mode,
            "area": area,
            "geometry": geometry,
            "progress": progress,
            "cancel": cancel,
            "apply_calib": apply_calib,
            "multi_exposure": multi_exposure,
            "infrared": infrared,
            "align_passes": align_passes,
            "me_exposures": me_exposures,
        }
        if getattr(self._model, "asic", "") == "GL128" and not self._gl128_primed:
            prime_dpi, prime_area = self._gl128_priming_geometry()
            if prime_area is None:
                prime_area = area
            if prime_dpi == 0:
                prime_dpi = resolution
            self._gl128_prime_pass(resolution=prime_dpi, area=prime_area)

        session = create_session(self._asic, self._model, self._calibrator)
        image = session.run(  # type: ignore[arg-type]
            **run_kwargs,
        )
        self._last_me_debug = getattr(session, "last_me_debug", None)
        return image

    def arm_bringup_motor(self) -> None:
        """Enable GL128 motor moves (default on for scan-ready GL128).

        Lab also uses this to re-arm after :meth:`disarm_bringup_motor` around
        stationary IR shading.
        """
        self._ensure_open()
        self._bringup_motor_armed = True
        if hasattr(self._asic, "_motor_moves_enabled"):
            self._asic._motor_moves_enabled = True
        logger.debug("Motor armed for %s", self._model.model)

    def disarm_bringup_motor(self) -> None:
        """Temporarily disable GL128 motor moves (stationary shading safety)."""
        self._bringup_motor_armed = False
        if hasattr(self._asic, "_motor_moves_enabled"):
            self._asic._motor_moves_enabled = False

    def _ensure_scan_ready(self) -> None:
        if getattr(self, "_allow_unvalidated_scan", False):
            return
        if self._bringup_motor_armed and getattr(self._model, "asic", "") == "GL128":
            return
        if not model_is_scan_ready(self._model):
            raise AsicError(
                f"{self._model.model} ({self._model.asic}) is locked out in this "
                "release: only OpticFilm 8200i SE (07b3:1825) and OpticFilm 8100 "
                "(V2) (07b3:1824) are validated for scanning. Open, status, lamp "
                "and register dumps still work."
            )

    def _ensure_open(self) -> None:
        if self._closed or not self._handle.is_open:
            raise PlustekError("Scanner is closed.")
