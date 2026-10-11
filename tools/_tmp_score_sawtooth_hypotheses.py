# SPDX-License-Identifier: GPL-3.0-or-later
"""Score sawtooth hypotheses against example PNGs (no HW).

Hypotheses:
  (a) channel misreg / thin green fringe + zoom (viewer)
  (b) period-2 / period-4 / Genesys (4,0) column Y stagger (driver)
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

EXAMPLES = {
    1440: Path(r"c:\Users\jbone\Desktop\examples\670542615-37951d43-d4ed-4221-87b9-fe74ba132abf.png"),
    2400: Path(r"c:\Users\jbone\Desktop\examples\670542601-5bf3e7e2-47df-404d-9d8e-27eb02cc38bd.png"),
    7200: Path(r"c:\Users\jbone\Desktop\examples\670542592-0a766093-59e2-4414-944c-af3dd52bde8f.png"),
    1800: Path(r"c:\Users\jbone\Desktop\examples\670542606-e826952f-7e87-43ba-a903-fc8987f6dbd4.png"),
}

LO, HI = 40, 220
EDGE = 48
H, W = 96, 320


def step_edge_rgb() -> np.ndarray:
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:EDGE] = HI
    rgb[EDGE:] = LO
    return rgb


def misreg_gb(rgb: np.ndarray, *, dg: int = 1, db: int = 1) -> np.ndarray:
    h, _, _ = rgb.shape
    out = np.zeros_like(rgb)
    out[:, :, 0] = rgb[:, :, 0]
    out[dg:, :, 1] = rgb[: h - dg, :, 1]
    out[db:, :, 2] = rgb[: h - db, :, 2]
    return out


def green_fringe_line(rgb: np.ndarray) -> np.ndarray:
    out = rgb.copy()
    out[EDGE, :, 0] = LO
    out[EDGE, :, 1] = HI
    out[EDGE, :, 2] = LO
    return out


def stagger_periodic(rgb: np.ndarray, pattern: tuple[int, ...]) -> np.ndarray:
    h, w, _ = rgb.shape
    max_s = max(pattern) if pattern else 0
    buf = np.zeros((h + max_s, w, 3), dtype=np.uint8)
    n = len(pattern)
    for x in range(w):
        s = pattern[x % n]
        buf[s : s + h, x, :] = rgb[:, x, :]
    return buf[:h]


def zoom(rgb: np.ndarray, factor: float, *, nearest: bool) -> np.ndarray:
    nh = max(1, round(rgb.shape[0] * factor))
    nw = max(1, round(rgb.shape[1] * factor))
    interp = cv2.INTER_NEAREST if nearest else cv2.INTER_CUBIC
    return cv2.resize(rgb, (nw, nh), interpolation=interp)


def edge_residual(rgb: np.ndarray, *, y0: int, y1: int, x0: int, x1: int) -> np.ndarray:
    crop = rgb[y0:y1, x0:x1].astype(np.float32)
    lum = crop.mean(axis=2)
    diff = lum[:-1] - lum[1:]
    ey = np.argmax(diff, axis=0).astype(np.float64)
    x = np.arange(len(ey), dtype=np.float64)
    coef = np.polyfit(x, ey, 1)
    return ey - (coef[0] * x + coef[1])


def autocorr_profile(resid: np.ndarray, max_lag: int = 32) -> np.ndarray:
    r = resid - resid.mean()
    if float(np.dot(r, r)) < 1e-9:
        return np.zeros(max_lag + 1, dtype=np.float64)
    corr = np.correlate(r, r, mode="full")
    mid = len(corr) // 2
    corr = corr[mid : mid + max_lag + 1].astype(np.float64)
    return corr / (corr[0] + 1e-9)


def first_peak_lag(corr: np.ndarray, *, min_lag: int = 2) -> tuple[int | None, float]:
    for k in range(min_lag, len(corr) - 1):
        if corr[k] > 0.2 and corr[k] >= corr[k - 1] and corr[k] >= corr[k + 1]:
            return k, float(corr[k])
    return None, float("nan")


def analyze(rgb: np.ndarray, *, y0: int, y1: int, x0: int, x1: int) -> dict:
    resid = edge_residual(rgb, y0=y0, y1=y1, x0=x0, x1=x1)
    corr = autocorr_profile(resid)
    lag, peak = first_peak_lag(corr)
    return {
        "corr": corr,
        "resid_std": float(resid.std()),
        "lag": lag,
        "peak": peak,
        "even_odd": float(resid[0::2].mean() - resid[1::2].mean()),
    }


def score(ref: dict, cand: dict) -> tuple[float, str]:
    if cand["resid_std"] < 0.15 * max(ref["resid_std"], 0.5):
        return 1e6, "flat"
    if ref["lag"] is None or cand["lag"] is None:
        lag_term = 5.0
    else:
        lag_term = abs(cand["lag"] - ref["lag"]) / max(ref["lag"], 1)
    std_term = abs(np.log((cand["resid_std"] + 1e-3) / (ref["resid_std"] + 1e-3)))
    n = min(len(ref["corr"]), len(cand["corr"]))
    spec = float(np.sqrt(np.mean((ref["corr"][1:n] - cand["corr"][1:n]) ** 2)))
    return float(2.0 * lag_term + std_term + 1.5 * spec), "ok"


def load_ref(dpi: int) -> dict | None:
    path = EXAMPLES[dpi]
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    info = analyze(rgb, y0=70, y1=min(h - 10, 520), x0=int(w * 0.15), x1=int(w * 0.85))
    info["dpi"] = dpi
    info["shape"] = rgb.shape
    return info


def all_candidates() -> list[tuple[str, str, np.ndarray]]:
    base = step_edge_rgb()
    out: list[tuple[str, str, np.ndarray]] = []
    for z in (3.0, 3.5, 4.0, 5.0, 7.0):
        m = misreg_gb(base)
        out.append(("a_misreg", f"misreg+nearest x{z:g}", zoom(m, z, nearest=True)))
        out.append(("a_misreg", f"misreg+bicubic x{z:g}", zoom(m, z, nearest=False)))
        g = green_fringe_line(base)
        out.append(("a_fringe", f"greenline+nearest x{z:g}", zoom(g, z, nearest=True)))
        out.append(("a_fringe", f"greenline+bicubic x{z:g}", zoom(g, z, nearest=False)))
        mg = green_fringe_line(misreg_gb(base))
        out.append(("a_combo", f"misreg+greenline+nearest x{z:g}", zoom(mg, z, nearest=True)))
        out.append(("a_combo", f"misreg+greenline+bicubic x{z:g}", zoom(mg, z, nearest=False)))
    patterns = (
        ((1, 0), "p2(1,0)"),
        ((0, 1), "p2(0,1)"),
        ((2, 0), "p2(2,0)"),
        ((1, 0, 0, 0), "p4(1,0,0,0)"),
        ((4, 0), "genesys(4,0)"),
    )
    for pat, label in patterns:
        st = stagger_periodic(base, pat)
        out.append(("b_stagger", f"{label} native", st))
        for z in (3.0, 3.5, 4.0, 5.0, 7.0):
            out.append(("b_stagger", f"{label} nearest x{z:g}", zoom(st, z, nearest=True)))
            out.append(("b_stagger", f"{label} bicubic x{z:g}", zoom(st, z, nearest=False)))
    return out


def main() -> None:
    cands = all_candidates()
    print("Scoring against example PNGs (flat candidates excluded from winners)\n")
    family_wins: dict[str, int] = {}
    for dpi in (1440, 2400, 7200, 1800):
        ref = load_ref(dpi)
        if ref is None:
            print(f"=== {dpi}: missing PNG ===")
            continue
        print(
            f"=== Real {dpi} PNG  resid_std={ref['resid_std']:.3f} "
            f"lag={ref['lag']} peak={ref['peak']:.3f} even-odd={ref['even_odd']:+.3f} ==="
        )
        if ref["resid_std"] < 0.2 or ref["lag"] is None:
            print("  (no measurable serration — skip ranking)\n")
            continue
        best_by_family: dict[str, tuple[float, str, dict]] = {}
        for family, name, img in cands:
            hh, ww = img.shape[:2]
            cand = analyze(
                img,
                y0=max(0, hh // 8),
                y1=min(hh, 7 * hh // 8),
                x0=ww // 8,
                x1=7 * ww // 8,
            )
            sc, why = score(ref, cand)
            if why != "ok":
                continue
            prev = best_by_family.get(family)
            if prev is None or sc < prev[0]:
                best_by_family[family] = (sc, name, cand)
        if not best_by_family:
            print("  no non-flat candidate matched\n")
            continue
        overall = min(best_by_family.items(), key=lambda kv: kv[1][0])
        for family, (sc, name, cand) in sorted(best_by_family.items(), key=lambda kv: kv[1][0]):
            mark = "  <-- WIN" if family == overall[0] else ""
            print(
                f"  {family:10s} score={sc:.4f}  {name:34s} "
                f"lag={cand['lag']} resid_std={cand['resid_std']:.3f}{mark}"
            )
        family_wins[overall[0]] = family_wins.get(overall[0], 0) + 1
        print()

    print("=== CROSS-PNG FAMILY WINS ===")
    for fam, n in sorted(family_wins.items(), key=lambda kv: -kv[1]):
        print(f"  {fam}: {n}")

    print("\n=== CODE CONTEXT (manual) ===")
    print("GL128 STAGGER_BY_DPI is empty; session_gl128 clears ASIC STAGGER bit.")
    print("Genesys host stagger (4,0) is 7200-only on GL845 — not wired for GL128.")
    print("If b_stagger wins on serrated PNGs: either missing host unstagger despite")
    print("clear ASIC bit, OR screenshot spectrum coincidentally matches stagger+zoom.")
    print("Need raw 100% TIFF to break the tie.")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
