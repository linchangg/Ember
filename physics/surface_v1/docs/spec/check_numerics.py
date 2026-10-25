#!/usr/bin/env python3
"""Slow standard-library LF/Euler checker for selected synthetic cases."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "fixtures" / "spec" / "numerical_smoke_results.json"


def hamiltonian(d, A, Bv, px, py):
    if px == 0.0 and py == 0.0:
        return 0.0
    ap = A[0] * px + A[1] * py
    bp = Bv[0] * px + Bv[1] * py
    return d[0] * px + d[1] * py + math.sqrt(ap * ap + bp * bp)


def alpha_bounds(d, A, Bv):
    return abs(d[0]) + math.sqrt(A[0] ** 2 + Bv[0] ** 2), abs(d[1]) + math.sqrt(A[1] ** 2 + Bv[1] ** 2)


def grid_centers(width, height, dx, x0=0.0, y0=0.0):
    xs, ys = [], []
    for r in range(height):
        for c in range(width):
            xs.append(x0 + (c + 0.5) * dx)
            ys.append(y0 + (r + 0.5) * dx)
    return xs, ys


def extract_points(phi, width, height, dx, valid, burnable):
    pts = []
    def idx(r, c):
        return r * width + c

    def ok(r, c):
        if r < 0 or c < 0 or r >= height or c >= width:
            return False
        i = idx(r, c)
        return valid[i] == 1 and burnable[i] == 1

    for r in range(height):
        for c in range(width):
            if not ok(r, c):
                continue
            i = idx(r, c)
            for dr, dc in ((0, 1), (1, 0)):
                r2, c2 = r + dr, c + dc
                if not ok(r2, c2):
                    continue
                j = idx(r2, c2)
                if phi[i] == 0.0:
                    pts.append(( (c + 0.5) * dx, (r + 0.5) * dx ))
                if (phi[i] > 0) == (phi[j] > 0) and phi[i] != 0 and phi[j] != 0:
                    continue
                if phi[i] == phi[j]:
                    continue
                lam = phi[i] / (phi[i] - phi[j])
                x = (c + 0.5) * dx + lam * ((c2 - c) * dx)
                y = (r + 0.5) * dx + lam * ((r2 - r) * dx)
                pts.append((x, y))
    # dedup
    uniq = []
    for p in pts:
        if not any(math.hypot(p[0] - q[0], p[1] - q[1]) <= 1e-9 for q in uniq):
            uniq.append(p)
    return uniq


def support_errors(points, center, analytic_H, n_dir=72):
    errs = []
    for k in range(n_dir):
        th = 2 * math.pi * k / n_dir
        n = (math.cos(th), math.sin(th))
        num = max((p[0] - center[0]) * n[0] + (p[1] - center[1]) * n[1] for p in points)
        errs.append(abs(num - analytic_H(n)))
    return max(errs), sum(errs) / len(errs)


def step_field(phi, width, height, dx, d, A, Bv, dt, spread_active, valid, burnable):
    ax, ay = alpha_bounds(d, A, Bv)
    n = width * height
    new = phi[:]
    def idx(r, c):
        return r * width + c

    def neighbor(r, c, i, rr, cc):
        if rr < 0 or cc < 0 or rr >= height or cc >= width:
            return phi[i]
        j = idx(rr, cc)
        if valid[j] != 1 or burnable[j] != 1:
            return phi[i]
        return phi[j]

    for r in range(height):
        for c in range(width):
            i = idx(r, c)
            if valid[i] != 1 or burnable[i] != 1:
                continue
            if not spread_active[i]:
                new[i] = phi[i]
                continue
            west = neighbor(r, c, i, r, c - 1)
            east = neighbor(r, c, i, r, c + 1)
            south = neighbor(r, c, i, r - 1, c)
            north = neighbor(r, c, i, r + 1, c)
            dxm = (phi[i] - west) / dx
            dxp = (east - phi[i]) / dx
            dym = (phi[i] - south) / dx
            dyp = (north - phi[i]) / dx
            px = 0.5 * (dxm + dxp)
            py = 0.5 * (dym + dyp)
            hlf = hamiltonian(d, A, Bv, px, py) - 0.5 * ax * (dxp - dxm) - 0.5 * ay * (dyp - dym)
            cand = phi[i] - dt * hlf
            new[i] = min(phi[i], cand)
    return new


def run_constant(width, height, dx, phi0, d, A, Bv, t_end, cfl=0.4, max_step=30.0, spread=None):
    n = width * height
    valid = [1] * n
    burnable = [1] * n
    spread_active = spread if spread is not None else [True] * n
    ax, ay = alpha_bounds(d, A, Bv)
    den = ax / dx + ay / dx
    dt_cfl = math.inf if den == 0 else cfl / den
    phi = phi0[:]
    t = 0.0
    while t < t_end - 1e-12:
        dt = min(dt_cfl, max_step, t_end - t)
        if not math.isfinite(dt) or dt <= 0:
            dt = min(max_step, t_end - t)
        phi = step_field(phi, width, height, dx, d, A, Bv, dt, spread_active, valid, burnable)
        t += dt
    return phi


def circle_case(dx: float) -> dict:
    domain = 2000.0
    width = height = int(round(domain / dx))
    r0 = 150.0
    xc = yc = domain / 2.0
    xs, ys = grid_centers(width, height, dx)
    phi0 = [math.hypot(xs[i] - xc, ys[i] - yc) - r0 for i in range(width * height)]
    R = 0.1
    d, A, Bv = (0.0, 0.0), (R, 0.0), (0.0, R)
    t_end = 1200.0
    phi = run_constant(width, height, dx, phi0, d, A, Bv, t_end)
    pts = extract_points(phi, width, height, dx, [1] * (width * height), [1] * (width * height))
    exact_r = r0 + R * t_end
    rad = [math.hypot(p[0] - xc, p[1] - yc) for p in pts]
    max_err = max(abs(r - exact_r) for r in rad)
    burned = sum(1 for v in phi if v <= 0.0)
    exact_area = math.pi * exact_r ** 2
    area = burned * dx * dx
    rel_area = abs(area - exact_area) / exact_area
    return {
        "case": "circle",
        "dx": dx,
        "max_boundary_error_m": max_err,
        "relative_burned_area_error": rel_area,
        "gate_max_boundary": 2 * dx,
        "pass": max_err <= 2 * dx + 1e-9 and rel_area <= 0.08,
    }


def ellipse_case(dx: float) -> dict:
    domain = 4000.0
    width = height = int(round(domain / dx))
    a, b = 0.2, 0.1
    c = math.sqrt(a * a - b * b)
    t0 = 1500.0
    t_end = 600.0
    cx0 = 2000.0 + c * t0
    xs, ys = grid_centers(width, height, dx)
    phi0 = []
    for i in range(width * height):
        val = b * (math.sqrt(((xs[i] - cx0) / a) ** 2 + ((ys[i] - 2000.0) / b) ** 2) - t0)
        phi0.append(val)
    d, A, Bv = (c, 0.0), (a, 0.0), (0.0, b)
    phi = run_constant(width, height, dx, phi0, d, A, Bv, t_end)
    pts = extract_points(phi, width, height, dx, [1] * (width * height), [1] * (width * height))
    t = t0 + t_end
    center = (2000.0 + c * t, 2000.0)

    def H(n):
        return hamiltonian(d, A, Bv, n[0], n[1]) * t

    max_err, mean_err = support_errors(pts, center, H)
    return {
        "case": "ellipse",
        "dx": dx,
        "max_support_error_m": max_err,
        "mean_support_error_m": mean_err,
        "gate_max_support": 2 * dx,
        "pass": max_err <= 2 * dx + 1e-9,
    }


def changing_case(dx: float) -> dict:
    domain = 2000.0
    width = height = int(round(domain / dx))
    r0 = 150.0
    xc = yc = domain / 2.0
    xs, ys = grid_centers(width, height, dx)
    phi0 = [math.hypot(xs[i] - xc, ys[i] - yc) - r0 for i in range(width * height)]
    a, b = 0.2, 0.1
    c = math.sqrt(a * a - b * b)
    d_e, A_e, Bv_e = (c, 0.0), (a, 0.0), (0.0, b)
    d_n, A_n, Bv_n = (0.0, c), (0.0, a), (-b, 0.0)
    phi = run_constant(width, height, dx, phi0, d_e, A_e, Bv_e, 300.0)
    phi = run_constant(width, height, dx, phi, d_n, A_n, Bv_n, 300.0)
    pts = extract_points(phi, width, height, dx, [1] * (width * height), [1] * (width * height))

    def H(n):
        return r0 + 300.0 * hamiltonian(d_e, A_e, Bv_e, n[0], n[1]) + 300.0 * hamiltonian(d_n, A_n, Bv_n, n[0], n[1])

    max_err, mean_err = support_errors(pts, (xc, yc), H)
    return {
        "case": "changing",
        "dx": dx,
        "max_support_error_m": max_err,
        "mean_support_error_m": mean_err,
        "gate_max_support": 3 * dx,
        "pass": max_err <= 3 * dx + 1e-9,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    full = args.full or not args.quick
    results = []
    results.append(circle_case(20.0))
    results.append(circle_case(10.0))
    if full:
        results.append(circle_case(5.0))
        results.append(ellipse_case(10.0))
        results.append(changing_case(10.0))
    else:
        results.append(ellipse_case(20.0))
        results.append(changing_case(20.0))
    payload = {"mode": "full" if full else "quick", "results": results}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    for r in results:
        print(r)
        if not r["pass"]:
            raise SystemExit(f"failed {r}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
