# SPDX-License-Identifier: GPL-3.0-or-later
"""Scan session for the OpticFilm 8200i SE (GL128).

Only the chip-specific steps are overridden; run/assemble/calibration lookup
stay in :class:`~pyopticfilm.scan.session.ScanSession`. What differs from GL845:

* the register block comes from the model tables plus a small set of
  resolution-dependent values, instead of being computed from a motor profile;
* motor slope tables are replayed from the capture rather than generated, so
  there is no ``zmod`` calculation;
* feeding is a separate, synchronous move before the scan starts;
* the image is announced with a single bulk preamble and then streamed, and the
  source is selected with ``wIndex`` — RAM for calibration passes, the live
  image stream for a scan.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from pyopticfilm.asic.registers import Gl128Registers
from pyopticfilm.device.model_8200i_se import MODEL_8200I_SE
from pyopticfilm.device.protocol import AsicDriver, FilmModel
from pyopticfilm.exceptions import AsicError, ScanCancelled, ScanError
from pyopticfilm.logging import get_logger
from pyopticfilm.scan.calibrate import Calibrator
from pyopticfilm.scan.geometry import ScanGeometry
from pyopticfilm.scan.session import DATA_TIMEOUT_S, ScanSession
from pyopticfilm.usb.device import BULK_MAX_SIZE

logger = get_logger(__name__)

#: Max bytes announced per ``bulk_read_begin``. SilverFast uses one preamble for
#: the whole image; we announce at most one USB bulk ceiling so a cancel never
#: leaves an incomplete Genesys DMA transfer (that wedges EP0 until power-cycle).
IMAGE_CHUNK_BYTES = BULK_MAX_SIZE

try:
    from pyopticfilm.asic.gl128 import MOTOR_GATED_HINT as _MOTOR_GATED_HINT
except ImportError:  # pragma: no cover
    _MOTOR_GATED_HINT = "GL128 motor moves are temporarily disabled."


class Gl128ScanSession(ScanSession):
    """GL128 scan state machine for the 8200i SE."""

    def __init__(
        self,
        asic: AsicDriver,
        model: FilmModel = MODEL_8200I_SE,
        calibrator: Calibrator | None = None,
    ) -> None:
        super().__init__(asic, model, calibrator)
        self.se_regs = Gl128Registers()
        #: Set when the image pass armed ``AGOHOME`` — wait for park in ``_end_scan``.
        self._await_agohome_park = False
        #: True after :meth:`bulk_read_begin` until :meth:`_end_scan` aborts/finishes.
        self._bulk_stream_active = False
        #: Image ``REG_EXPOSURE`` for the current pass (``None`` → short / 14000).
        self._pass_exposure: int | None = None

    def run(
        self,
        *args,
        multi_exposure: bool = False,
        infrared: bool = False,
        merge: str = "none",
        align_passes: bool = True,
        **kwargs,
    ):  # type: ignore[no-untyped-def]
        """Refuse unless the ASIC explicitly arms motor moves."""
        if not getattr(self.asic, "_motor_moves_enabled", False):
            raise AsicError(_MOTOR_GATED_HINT)
        if multi_exposure or (infrared and kwargs.get("mode", "color") == "color"):
            return self._run_multi_pass(
                *args,
                multi_exposure=multi_exposure,
                infrared=infrared,
                merge=merge,
                align_passes=align_passes,
                **kwargs,
            )
        return super().run(*args, **kwargs)

    # --- configure ------------------------------------------------------

    def _configure(self, geometry: ScanGeometry) -> None:
        r = self.se_regs
        model = self.model
        dpi = geometry.resolution
        shading = bool(geometry.disable_buffer_full_move)

        cache = model.boot_register_map()
        cache.update(model.gpo_regs)
        cache.update(model.sensor_custom_regs)

        try:
            asic_dpi = model.asic_dpi_for(dpi)
            cache[0x2B] = model.dummy_by_dpi[asic_dpi]
            exp_fn = getattr(model, "image_exposure", None)
            if self._pass_exposure is not None:
                exposure = int(self._pass_exposure)
            elif callable(exp_fn):
                exposure = int(exp_fn(long_exposure=False))
            else:
                exposure = int(model.exposure_lperiod)
            long_pass = exposure >= int(getattr(model, "exposure_long", exposure * 2))
            clk_fn = getattr(model, "pixel_clock_for_image", None)
            if callable(clk_fn):
                clk = int(clk_fn(dpi, long_exposure=long_pass))
            else:
                clk = int(model.pixel_clock_by_dpi[asic_dpi])
            cache[0xA5] = clk
            cache[0xAB] = clk
        except KeyError as exc:
            raise ScanError(
                f"No capture-derived register values for {dpi} dpi on "
                f"{model.model}; supported: {sorted(model.resolutions_dpi)}"
            ) from exc

        # Image: DEPTH8 *registers* (session 11) but 16-bit LE samples on the
        # wire. Calib/shading: DEPTH16 regs + 16-bit samples (sessions 03–04).
        if shading:
            cache[r.REG_DEPTH_A] = r.DEPTH16_A
            cache[r.REG_DEPTH_B] = r.DEPTH16_B
        else:
            cache[r.REG_DEPTH_A] = r.DEPTH8_A
            cache[r.REG_DEPTH_B] = r.DEPTH8_B

        # Host shading: clear DVDSET on calib. Colour image keeps DVDSET only
        # when a measured ASIC shading table is ready — otherwise boot DVDSET +
        # unity or stale coefficients produce rainbow / clipped garbage.
        # Infrared must never keep colour DVDSET: live HW clips IR to full scale
        # with an IR table, and a colour table after a colour+IR pair makes the
        # IR frame magenta, uneven, and low-contrast for dust.
        reg01 = (cache.get(r.REG_0x01, 0x22) | r.SHDAREA) & ~r.SCAN & ~r.STAGGER
        infrared = getattr(self.asic, "_scan_method", None) == "infrared"
        if shading or infrared or not getattr(self.asic, "asic_shading_ready", False):
            reg01 &= ~r.DVDSET
        cache[r.REG_0x01] = reg01

        # Leave lamp / IR LED alone — :meth:`Gl128.lamp_on` already programmed
        # ``0x03`` / ``0x37`` for the scan method; rewriting them from the boot
        # cache would clear IR LED or re-enable the white lamp.
        cache.pop(r.REG_0x03, None)
        cache.pop(r.REG_IR, None)

        motor = r.MTRPWR
        if not shading:
            # Image pass: AGOHOME parks the carriage when the scan ends.
            motor |= r.AGOHOME
        cache[r.REG_0x02] = motor

        self._set24(cache, r.REG_LINCNT, geometry.lincnt_register)
        self._set24(cache, r.REG_LPERIOD, model.line_period_for(dpi))
        # Captures: AFE/shading always use DPISET = optical_resolution/6 (1200).
        dpiset = (
            model.optical_resolution // 6 if shading else geometry.register_dpiset
        )
        self._set16(cache, r.REG_DPISET, dpiset)
        self._set24(cache, r.REG_STRPIXEL, geometry.pixel_startx)
        self._set24(cache, r.REG_ENDPIXEL, geometry.pixel_endx)
        if self._pass_exposure is not None:
            exposure_reg = int(self._pass_exposure)
        else:
            exp_fn = getattr(model, "image_exposure", None)
            exposure_reg = (
                int(exp_fn(long_exposure=False))
                if callable(exp_fn)
                else int(model.exposure_lperiod)
            )
        self._set24(cache, r.REG_EXPOSURE, exposure_reg)
        # Image/calib acquire with FEEDL=1; positioning is a separate feed pair.
        self._set24(cache, r.REG_FEEDL, 1)
        cache.pop(r.REG_CLRCNT, None)
        cache.pop(r.REG_START, None)

        self._await_agohome_park = not shading and bool(motor & r.AGOHOME)

        # Capture-constant feeds from home — never geometry.starty (that was the
        # grinding bug). Calibration passes stay put (no motor). Positioning is
        # skipped while motor moves are gated so configure unit tests stay safe.
        if not shading and getattr(self.asic, "_motor_moves_enabled", False):
            feed_fn = getattr(model, "feed_to_scan_steps_for_area", None)
            scan_steps = (
                feed_fn(geometry.area)
                if callable(feed_fn)
                else model.feed_to_scan_steps
            )
            # The scan must stop at the window end: feed2 + travel <= 27636
            # steps. Overrunning it is what ground the motor in the Lab.
            max_fn = getattr(model, "max_lincnt_for", None)
            max_lc = max_fn(scan_steps, dpi) if callable(max_fn) else None
            if max_lc is not None and geometry.lincnt_register > int(max_lc):
                start_mm = scan_steps * 25.4 / model.feed_steps_per_inch
                raise ScanError(
                    f"Image LINCNT {geometry.lincnt_register} at {dpi} dpi is "
                    f"{geometry.travel_mm:.1f} mm of travel from feed2="
                    f"{scan_steps} ({start_mm:.1f} mm), past the "
                    f"{model.scan_window_end_steps * 25.4 / model.feed_steps_per_inch:.1f} mm "
                    f"scan-window end. Max LINCNT here is {max_lc} "
                    "(see captures/8200i-se/MOTOR.md)."
                )
            self.asic.position_for_full_frame_scan(scan_steps=scan_steps)

        ch_exp_fn = getattr(model, "channel_exposure_for", None)
        if callable(ch_exp_fn):
            try:
                channel_exp = int(ch_exp_fn(dpi, exposure=exposure_reg))
            except TypeError:
                channel_exp = int(ch_exp_fn(dpi))
        else:
            channel_exp = None
        self.asic.upload_tables(
            resolution=dpi, shading=shading, channel_exposure=channel_exp
        )
        # Do NOT call set_frontend_init() here — boot zeroes FE gains, and
        # replaying that after search_afe undoes calibration. Captures keep the
        # post-calib FE for the image pass. Re-apply the last search result if
        # we have one (covers any FE touch during table upload / strip setup).
        last_afe = getattr(self.asic, "last_afe", None)
        if last_afe is not None:
            self.asic.apply_frontend(last_afe)

        self.asic.protocol.write_registers_batched(sorted(cache.items()))
        self.asic._reg_cache.update(cache)

        if self._lamp_requested:
            self.asic.lamp_on()
        else:
            self.asic.lamp_off()

        # Base class feed-wait uses this; GL128 feeds synchronously above.
        self._feedl = 0

        logger.info(
            "GL128 configured %ddpi dpiset=%d lincnt=%d str=%d end=%d lperiod=%d "
            "shading=%s exposure=%d",
            dpi,
            dpiset,
            geometry.lincnt_register,
            geometry.pixel_startx,
            geometry.pixel_endx,
            model.line_period_for(dpi),
            shading,
            exposure_reg,
        )

    # --- multi-pass (ME / IR) -------------------------------------------

    def _run_multi_pass(
        self,
        *,
        resolution: int = 1800,
        mode: str = "color",
        area: tuple[float, float, float, float] | None = None,
        geometry: ScanGeometry | None = None,
        progress: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
        apply_calib: bool = True,
        multi_exposure: bool = False,
        infrared: bool = False,
        merge: str = "none",
        align_passes: bool = True,
    ):
        from pyopticfilm.image import ScanImage
        from pyopticfilm.pass_align import align_pass_to_reference
        from pyopticfilm.scan.exposure_merge import merge_exposures_result
        from pyopticfilm.scan.geometry import compute_geometry

        if mode == "infrared":
            raise ValueError("Use mode='color' with infrared=True for colour+IR scans")
        if merge not in {"none", "linear", "fusion"}:
            raise ValueError(f"Unsupported merge {merge!r}")
        if merge != "none" and not multi_exposure:
            raise ValueError("merge requires multi_exposure=True")

        model = self.model
        exp_short = int(getattr(model, "exposure_short", model.exposure_lperiod))
        exp_long = int(getattr(model, "exposure_long", exp_short * 3))

        if not self.asic._initialized:
            self.asic.init()

        if geometry is None:
            geometry = compute_geometry(resolution, model=model, area=area)

        passes: list[tuple[str, str, int, bool]] = [
            ("color_short", "transparency", exp_short, False),
        ]
        if infrared:
            passes.append(("ir", "infrared", exp_short, False))
        if multi_exposure:
            passes.append(("color_long", "transparency", exp_long, True))

        logger.info(
            "GL128 multi-pass %ddpi passes=%d me=%s ir=%s merge=%s",
            geometry.resolution,
            len(passes),
            multi_exposure,
            infrared,
            merge,
        )

        rgb_short = None
        rgb_long = None
        ir_plane = None
        n_pass = len(passes)

        for idx, (key, method, exposure, remeasure) in enumerate(passes):

            def _prog(p: float, _i: int = idx) -> None:
                if progress is not None:
                    progress(min(1.0, (_i + p) / n_pass))

            calib = None
            if apply_calib and self.calibrator is not None:
                if method == "transparency":
                    if remeasure:
                        self.asic.asic_shading_ready = False  # type: ignore[attr-defined]
                        calib = self.calibrator.measure_colour_asic_shading(geometry)
                    else:
                        calib = self.calibrator.ensure_colour_asic_shading(geometry)
                else:
                    calib = self.calibrator.find_for_scan(
                        method=method, geometry=geometry
                    )
                    if calib is None:
                        logger.warning(
                            "No calib cache for method=%s dpi=%d — scanning uncalibrated.",
                            method,
                            geometry.resolution,
                        )

            self._pass_exposure = exposure
            raw = self.acquire_raw(
                geometry,
                method=method,
                lamp_on=True,
                start_motor=True,
                progress=_prog,
                cancel=cancel,
            )
            self._pass_exposure = None

            use_host = (
                calib is not None
                and self.calibrator is not None
                and self.calibrator.should_apply_host_calib()
            )
            dark = calib.dark if use_host else None
            white = calib.white if use_host else None
            planar = getattr(self.asic, "usb_planar_rgb", None)
            if planar is None:
                planar = bool(getattr(self.model, "usb_planar_rgb", False))
            rgb = self.pipeline.assemble(
                raw, geometry, dark=dark, white=white, planar=bool(planar)
            )

            if key == "color_short":
                rgb_short = rgb
            elif key == "color_long":
                rgb_long = rgb
            elif key == "ir":
                ir_plane = self._infrared_plane(rgb)

        assert rgb_short is not None
        align_shift_long: tuple[int, int] | None = None
        align_shift_ir: tuple[int, int] | None = None

        if align_passes and rgb_long is not None:
            _, align_shift_long = align_pass_to_reference(rgb_short, rgb_long)
        if align_passes and ir_plane is not None:
            ir_plane, align_shift_ir = align_pass_to_reference(rgb_short, ir_plane)

        primary = rgb_short
        merge_method: str | None = None
        merge_fusion_mean_short_weight: float | None = None
        merge_fusion_mean_long_weight: float | None = None
        merge_fusion_zero_weight_fraction: float | None = None
        if multi_exposure and merge != "none" and rgb_long is not None:
            merge_method = merge
            shift = align_shift_long if align_passes else (0, 0)
            merged = merge_exposures_result(
                rgb_short,
                rgb_long,
                method=merge,  # type: ignore[arg-type]
                exposure_short=exp_short,
                exposure_long=exp_long,
                align_shift=shift,
            )
            primary = merged.rgb
            if merged.fusion_stats is not None:
                merge_fusion_mean_short_weight = merged.fusion_stats.mean_short_weight
                merge_fusion_mean_long_weight = merged.fusion_stats.mean_long_weight
                merge_fusion_zero_weight_fraction = merged.fusion_stats.zero_weight_fraction

        return ScanImage(
            rgb=primary,
            dpi=geometry.resolution,
            device_model=f"{self.model.vendor} {self.model.model}",
            ir=ir_plane,
            rgb_short=rgb_short,
            rgb_long=rgb_long,
            exposure_short=exp_short if multi_exposure else None,
            exposure_long=exp_long if multi_exposure else None,
            merge_method=merge_method,
            merge_fusion_mean_short_weight=merge_fusion_mean_short_weight,
            merge_fusion_mean_long_weight=merge_fusion_mean_long_weight,
            merge_fusion_zero_weight_fraction=merge_fusion_zero_weight_fraction,
            align_shift_long=align_shift_long,
            align_shift_ir=align_shift_ir,
        )

    # --- acquire --------------------------------------------------------

    def _begin_scan(self, *, start_motor: bool = True) -> None:
        """Capture order: ``0x0d=0x07`` → set SCAN → ``0x0f`` (session 03)."""
        r = self.se_regs
        proto = self.asic.protocol

        proto.write_register(r.REG_CLRCNT, r.CLRCNT_ALL)
        reg01 = proto.read_register(r.REG_0x01) | r.SCAN
        proto.write_register(r.REG_0x01, reg01)
        self.asic._reg_cache[r.REG_0x01] = reg01
        proto.write_register(r.REG_START, r.START_GO if start_motor else 0x00)
        logger.info("GL128 scan started motor=%s", start_motor)

    def _wait_data(self, cancel: threading.Event | None) -> None:
        """Wait until the ASIC reports data in its buffer.

        GL845 also cross-checks the valid-word counters at ``0x42``-``0x45``;
        the SE captures never touch those, so buffer state is all there is.
        """
        deadline = time.monotonic() + DATA_TIMEOUT_S
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled("cancelled waiting for data")
            if not self.asic.read_status().is_buffer_empty:
                return
            time.sleep(0.02)
        raise ScanError(f"No scan data within {DATA_TIMEOUT_S:.0f}s")

    def _acquire(
        self,
        geometry: ScanGeometry,
        *,
        progress: Callable[[float], None] | None,
        cancel: threading.Event | None,
        wait_feed: bool = True,
    ) -> bytes:
        del wait_feed  # GL128 feeds synchronously in _configure
        self._wait_data(cancel)

        r = self.se_regs
        proto = self.asic.protocol
        total = geometry.total_bytes
        index = (
            r.BULK_INDEX_RAM
            if geometry.disable_buffer_full_move
            else r.BULK_INDEX_IMAGE
        )
        index = (
            r.BULK_INDEX_RAM
            if geometry.disable_buffer_full_move
            else r.BULK_INDEX_IMAGE
        )

        buf = bytearray()
        while len(buf) < total:
            if cancel is not None and cancel.is_set():
                raise ScanCancelled("cancelled during bulk read")
            # Announce only this USB-sized block and fully drain it before the
            # next cancel check — avoids mid-DMA EP0 wedge on Stop.
            want = min(IMAGE_CHUNK_BYTES, total - len(buf))
            proto.bulk_read_begin(want, index=index)
            self._bulk_stream_active = True
            try:
                chunk = proto.bulk_read_exact(want)
            finally:
                self._bulk_stream_active = False
            if not chunk:
                raise ScanError(
                    f"Bulk stream ended after {len(buf)} of {total} bytes"
                )
            buf.extend(chunk)
            if progress is not None:
                progress(min(1.0, len(buf) / total))

        if progress is not None:
            progress(1.0)
        return bytes(buf[:total])

    def _end_scan(self) -> None:
        # Capture end/cancel recipe (lamp strobe + clear SCAN + AGOHOME park)
        # lives in Gl128.stop_motor — do not bare-clear 0x01 here or the strobe
        # order is lost and SCAN is cleared twice.
        #
        # Mid-bulk cancel must stop the ASIC first, then abort the host bulk IN
        # pipe; otherwise the next Scanner.open()/init control transfers time out
        # until power-cycle (Phase 2 repro).
        try:
            # Stop ASIC DMA first (clear SCAN / AGOHOME park), then abort the
            # host bulk IN so the pipe is not left half-open across close/reopen.
            super()._end_scan()
        finally:
            if self._bulk_stream_active:
                self._bulk_stream_active = False
                try:
                    drained = self.asic.protocol.abort_bulk_stream()
                    logger.info("GL128 bulk abort after end_scan drained=%d", drained)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("GL128 bulk abort after end_scan: %s", exc)
            self._await_agohome_park = False
