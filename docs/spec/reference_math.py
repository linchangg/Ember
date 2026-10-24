#!/usr/bin/env python3
"""Executable arithmetic/analytic reference for surface_v1 equations.

Python standard library only. Generated values are derived from this
specification, not from an independent Behave/FARSITE oracle.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

ATOL = 1e-10
RTOL = 1e-8
FT_PER_M = 1.0 / 0.3048
LB_FT2_PER_KG_M2 = 1.0 / 4.88242763638305
LB_FT3_PER_KG_M3 = 1.0 / 16.0184633739601
SAV_FT_PER_SAV_M = 0.3048
J_PER_KG_PER_BTU_LB = 2326.00032491728
FT_MIN_PER_M_S = 60.0 / 0.3048
M_S_PER_FT_MIN = 0.3048 / 60.0
KW_M2_PER_BTU_FT2_MIN = 1055.05585262 / (0.09290304 * 60.0 * 1000.0)
KW_M_PER_BTU_FT_S = 1055.05585262 / (0.3048 * 1000.0)

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "docs" / "spec" / "fuel_models.csv"
OUT_JSON = ROOT / "fixtures" / "spec" / "reference_values.json"


def nearly_equal(a: float, b: float, atol: float = ATOL, rtol: float = RTOL) -> bool:
    return abs(a - b) <= atol + rtol * max(abs(a), abs(b))


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def sav_bin(sigma: float) -> int | None:
    if sigma >= 1200:
        return 0
    if 192 <= sigma < 1200:
        return 1
    if 96 <= sigma < 192:
        return 2
    if 48 <= sigma < 96:
        return 3
    if 16 <= sigma < 48:
        return 4
    return None


def load_catalog(path: Path = CATALOG_PATH) -> dict[int, dict]:
    models = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            mid = int(row["fuel_id"])
            models[mid] = {
                "fuel_id": mid,
                "code": row["code"],
                "name": row["name"],
                "is_dynamic": row["is_dynamic"].strip() in {"1", "true", "True"},
                "depth_ft": float(row["depth_ft"]),
                "mx_dead": float(row["mx_dead"]),
                "heat_dead": float(row["heat_dead_btu_lb"]),
                "heat_live": float(row["heat_live_btu_lb"]),
                "w1h": float(row["w1h_lb_ft2"]),
                "w10h": float(row["w10h_lb_ft2"]),
                "w100h": float(row["w100h_lb_ft2"]),
                "wlh": float(row["wlh_lb_ft2"]),
                "wlw": float(row["wlw_lb_ft2"]),
                "sav1h": float(row["sav1h_per_ft"]),
                "savlh": float(row["savlh_per_ft"]),
                "savlw": float(row["savlw_per_ft"]),
                "rho_p": float(row["rho_p_lb_ft3"]),
                "s_t": float(row["s_t"]),
                "s_e": float(row["s_e"]),
                "sav10h": float(row["sav10h_per_ft"]),
                "sav100h": float(row["sav100h_per_ft"]),
            }
    return models


def curing_fraction(m_lh: float) -> float:
    return clamp((1.20 - m_lh) / 0.90, 0.0, 1.0)


def particles_for(model: dict, moisture: tuple[float, float, float, float, float]) -> list[dict]:
    m1, m10, m100, mlh, mlw = moisture
    wlh_orig = model["wlh"]
    if model["is_dynamic"]:
        fc = curing_fraction(mlh)
        w_cured = fc * wlh_orig
        w_lh = (1.0 - fc) * wlh_orig
    else:
        fc = 0.0
        w_cured = 0.0
        w_lh = wlh_orig

    parts = [
        ("dead", "1h", model["w1h"], model["sav1h"], m1, model["heat_dead"]),
        ("dead", "10h", model["w10h"], model["sav10h"], m10, model["heat_dead"]),
        ("dead", "100h", model["w100h"], model["sav100h"], m100, model["heat_dead"]),
        ("dead", "cured_herb", w_cured, model["savlh"], m1, model["heat_dead"]),
        ("live", "herb", w_lh, model["savlh"], mlh, model["heat_live"]),
        ("live", "woody", model["wlw"], model["savlw"], mlw, model["heat_live"]),
    ]
    out = []
    for life, name, w, sigma, m, h in parts:
        if w <= 0.0:
            continue
        out.append(
            {
                "life": life,
                "name": name,
                "w": w,
                "sigma": sigma,
                "rho_p": model["rho_p"],
                "h": h,
                "s_t": model["s_t"],
                "s_e": model["s_e"],
                "m": m,
            }
        )
    return out, fc


def local_behavior(model: dict, moisture: tuple[float, float, float, float, float],
                   wind_east_m_s: float = 0.0, wind_north_m_s: float = 0.0,
                   gx: float = 0.0, gy: float = 0.0,
                   wind_limit: str = "effective_0p9_ir",
                   ellipse_max_lb: float = 8.0) -> dict:
    parts, fc = particles_for(model, moisture)
    orig_load = model["w1h"] + model["w10h"] + model["w100h"] + model["wlh"] + model["wlw"]
    trans_load = sum(p["w"] for p in parts)
    require(nearly_equal(orig_load, trans_load, atol=0.0, rtol=1e-12) or orig_load == 0.0,
            "load not conserved")

    zero = {
        "cured_fraction": fc,
        "sigma": 0.0,
        "beta": 0.0,
        "r_beta": 0.0,
        "gamma": 0.0,
        "ir": 0.0,
        "h_sink": 0.0,
        "r0_ft_min": 0.0,
        "r0_m_s": 0.0,
        "r_head_m_s": 0.0,
        "r_back_m_s": 0.0,
        "lb": 1.0,
        "eccentricity": 0.0,
        "a": 0.0,
        "b": 0.0,
        "c": 0.0,
        "phi_w": 0.0,
        "phi_s": 0.0,
        "phi_eff": 0.0,
        "u_eff_ft_min": 0.0,
        "wind_limited": False,
        "lb_clamped": False,
        "spread_active": False,
        "intermediates": {},
    }
    if not parts:
        return zero

    delta = model["depth_ft"]
    mx_d = model["mx_dead"]
    require(delta > 0.0 and mx_d > 0.0, "invalid fuel parameter")
    for p in parts:
        require(p["sigma"] > 0.0 and p["rho_p"] > 0.0, "invalid particle")
        require(p["s_e"] > 0.0, "S_e<=0")

    groups = {"dead": [], "live": []}
    for p in parts:
        groups[p["life"]].append(p)

    def area(p):
        return p["w"] * p["sigma"] / p["rho_p"]

    A_l = {life: sum(area(p) for p in plist) for life, plist in groups.items()}
    A_total = sum(A_l.values())
    F_l = {life: (A_l[life] / A_total if A_total > 0 else 0.0) for life in groups}
    f_lj = {}
    for life, plist in groups.items():
        for p in plist:
            f_lj[id(p)] = area(p) / A_l[life] if A_l[life] > 0 else 0.0

    sigma = 0.0
    for life, plist in groups.items():
        inner = sum(f_lj[id(p)] * p["sigma"] for p in plist)
        sigma += F_l[life] * inner

    def wn(plist):
        g = [0.0] * 5
        for p in plist:
            b = sav_bin(p["sigma"])
            if b is not None:
                g[b] += f_lj[id(p)]
        total = 0.0
        for p in plist:
            b = sav_bin(p["sigma"])
            if b is None:
                continue
            total += g[b] * p["w"] * (1.0 - p["s_t"])
        return total

    Wn = {life: wn(plist) for life, plist in groups.items()}
    Mbar = {}
    hbar = {}
    Sebar = {}
    for life, plist in groups.items():
        Mbar[life] = sum(f_lj[id(p)] * p["m"] for p in plist)
        hbar[life] = sum(f_lj[id(p)] * p["h"] for p in plist)
        Sebar[life] = sum(f_lj[id(p)] * p["s_e"] for p in plist)

    wsum = sum(p["w"] for p in parts)
    rho_b = wsum / delta
    beta = sum(p["w"] / (delta * p["rho_p"]) for p in parts)
    require(beta > 0.0, "nonpositive packing")
    beta_op = 3.348 * sigma ** (-0.8189)
    r_beta = beta / beta_op

    D = sum(p["w"] * math.exp(-138.0 / p["sigma"]) for p in groups["dead"])
    L = sum(p["w"] * math.exp(-500.0 / p["sigma"]) for p in groups["live"])
    if D > 0:
        Mfd = sum(p["w"] * math.exp(-138.0 / p["sigma"]) * p["m"] for p in groups["dead"]) / D
    else:
        Mfd = 0.0
    if groups["live"] and L > 0:
        Mx_l = max(mx_d, 2.9 * (D / L) * (1.0 - Mfd / mx_d) - 0.226)
    else:
        Mx_l = mx_d
    Mx = {"dead": mx_d, "live": Mx_l}

    eta_M = {}
    eta_S = {}
    for life, plist in groups.items():
        if not plist:
            eta_M[life] = 0.0
            eta_S[life] = 0.0
            continue
        r_M = Mbar[life] / Mx[life]
        if r_M >= 1.0:
            eta = 0.0
        else:
            eta = 1.0 - 2.59 * r_M + 5.11 * r_M ** 2 - 3.52 * r_M ** 3
        eta_M[life] = clamp(eta, 0.0, 1.0)
        eta_S[life] = min(1.0, 0.174 * Sebar[life] ** (-0.19))

    a_gamma = 133.0 * sigma ** (-0.7913)
    gamma_max = (sigma ** 1.5) / (495.0 + 0.0594 * (sigma ** 1.5))
    gamma = gamma_max * math.exp(a_gamma * (math.log(r_beta) + 1.0 - r_beta))
    IR_l = {}
    for life in groups:
        IR_l[life] = gamma * Wn[life] * hbar[life] * eta_M[life] * eta_S[life]
    IR = IR_l["dead"] + IR_l["live"]

    if IR == 0.0:
        out = dict(zero)
        out.update({
            "cured_fraction": fc,
            "sigma": sigma,
            "beta": beta,
            "r_beta": r_beta,
            "gamma": gamma,
            "ir": 0.0,
            "intermediates": {
                "Wn": Wn, "Mbar": Mbar, "eta_M": eta_M, "eta_S": eta_S,
                "Mx_l": Mx_l, "Mfd": Mfd, "F_l": F_l, "beta_op": beta_op,
                "rho_b": rho_b, "IR_l": IR_l, "A_l": A_l,
            },
        })
        return out

    h_sink = 0.0
    for life, plist in groups.items():
        inner = 0.0
        for p in plist:
            eps = math.exp(-138.0 / p["sigma"])
            qig = 250.0 + 1116.0 * p["m"]
            inner += f_lj[id(p)] * eps * qig
        h_sink += rho_b * F_l[life] * inner
    require(h_sink > 0.0, "nonpositive heat sink")
    xi = math.exp((0.792 + 0.681 * math.sqrt(sigma)) * (beta + 0.1)) / (192.0 + 0.2595 * sigma)
    r0_ft_min = IR * xi / h_sink
    r0_m_s = r0_ft_min * M_S_PER_FT_MIN

    C = 7.47 * math.exp(-0.133 * sigma ** 0.55)
    B = 0.02526 * sigma ** 0.54
    E = 0.715 * math.exp(-0.000359 * sigma)
    U = math.hypot(wind_east_m_s, wind_north_m_s)
    U_ft_min = U * FT_MIN_PER_M_S
    phi_w = 0.0 if U == 0.0 else C * (U_ft_min ** B) * (r_beta ** (-E))
    s = math.hypot(gx, gy)
    phi_s = 5.275 * (beta ** (-0.3)) * (s ** 2)
    q = math.sqrt(1.0 + s ** 2)
    if s > 1e-12:
        e_up = (gx / (s * q), gy / (s * q), s / q)
        e_cross = (-gy / s, gx / s, 0.0)
    else:
        e_up = (1.0, 0.0, 0.0)
        e_cross = (0.0, 1.0, 0.0)

    def lift(dxh, dyh):
        vz = gx * dxh + gy * dyh
        nrm = math.sqrt(dxh * dxh + dyh * dyh + vz * vz)
        return (dxh / nrm, dyh / nrm, vz / nrm)

    if U > 0.0:
        w_hat = lift(wind_east_m_s / U, wind_north_m_s / U)
    else:
        w_hat = (0.0, 0.0, 0.0)
    v_phi = tuple(phi_w * w_hat[i] + phi_s * e_up[i] for i in range(3))
    phi_eff_raw = math.sqrt(sum(v * v for v in v_phi))
    if phi_eff_raw > 1e-12:
        hvec = tuple(v / phi_eff_raw for v in v_phi)
    else:
        hvec = e_up
    nvec = (-gx, -gy, 1.0)
    nn = math.sqrt(sum(v * v for v in nvec))
    nvec = tuple(v / nn for v in nvec)
    kvec = (
        nvec[1] * hvec[2] - nvec[2] * hvec[1],
        nvec[2] * hvec[0] - nvec[0] * hvec[2],
        nvec[0] * hvec[1] - nvec[1] * hvec[0],
    )
    kn = math.sqrt(sum(v * v for v in kvec))
    kvec = tuple(v / kn for v in kvec)

    if phi_eff_raw > 0.0:
        u_eff_raw = (phi_eff_raw / (C * (r_beta ** (-E)))) ** (1.0 / B)
    else:
        u_eff_raw = 0.0
    u_limit = 0.9 * IR
    wind_limited = False
    if wind_limit == "effective_0p9_ir":
        u_eff = min(u_eff_raw, u_limit)
        wind_limited = u_eff_raw > u_limit + 0.0 and u_eff_raw > 0.0
    elif wind_limit == "none":
        u_eff = u_eff_raw
    else:
        raise ValueError(wind_limit)
    phi_eff = 0.0 if u_eff == 0.0 else C * (u_eff ** B) * (r_beta ** (-E))
    r_head_ft_min = r0_ft_min * (1.0 + phi_eff)
    r_head_m_s = r_head_ft_min * M_S_PER_FT_MIN
    ue = u_eff * M_S_PER_FT_MIN
    lb_raw = 0.936 * math.exp(0.2566 * ue) + 0.461 * math.exp(-0.1548 * ue) - 0.397
    lb = clamp(lb_raw, 1.0, ellipse_max_lb)
    lb_clamped = lb != lb_raw
    e = math.sqrt(max(0.0, 1.0 - 1.0 / (lb ** 2)))
    r_back_m_s = r_head_m_s * (1.0 - e) / (1.0 + e)
    a = (r_head_m_s + r_back_m_s) / 2.0
    b = a / lb
    c = (r_head_m_s - r_back_m_s) / 2.0

    return {
        "cured_fraction": fc,
        "sigma": sigma,
        "beta": beta,
        "r_beta": r_beta,
        "gamma": gamma,
        "ir": IR,
        "h_sink": h_sink,
        "xi": xi,
        "r0_ft_min": r0_ft_min,
        "r0_m_s": r0_m_s,
        "r_head_m_s": r_head_m_s,
        "r_back_m_s": r_back_m_s,
        "lb": lb,
        "lb_raw": lb_raw,
        "eccentricity": e,
        "a": a,
        "b": b,
        "c": c,
        "phi_w": phi_w,
        "phi_s": phi_s,
        "phi_eff": phi_eff,
        "u_eff_ft_min": u_eff,
        "wind_limited": wind_limited,
        "lb_clamped": lb_clamped,
        "spread_active": r_head_m_s > 0.0,
        "h": hvec,
        "k": kvec,
        "e_up": e_up,
        "e_cross": e_cross,
        "intermediates": {
            "Wn": Wn,
            "Mbar": Mbar,
            "eta_M": eta_M,
            "eta_S": eta_S,
            "Mx_l": Mx_l,
            "Mfd": Mfd,
            "F_l": F_l,
            "beta_op": beta_op,
            "rho_b": rho_b,
            "IR_l": IR_l,
            "A_l": A_l,
            "C": C,
            "B": B,
            "E": E,
            "xi": xi,
        },
    }


def hamiltonian(d, A, Bv, p):
    px, py = p
    if px == 0.0 and py == 0.0:
        return 0.0
    dp = d[0] * px + d[1] * py
    ap = A[0] * px + A[1] * py
    bp = Bv[0] * px + Bv[1] * py
    return dp + math.sqrt(ap * ap + bp * bp)


def alpha_bounds(d, A, Bv):
    ax = abs(d[0]) + math.sqrt(A[0] ** 2 + Bv[0] ** 2)
    ay = abs(d[1]) + math.sqrt(A[1] ** 2 + Bv[1] ** 2)
    return ax, ay


def wind_from_bearing(speed_m_s: float, from_deg: float):
    rad = from_deg * math.pi / 180.0
    return (-speed_m_s * math.sin(rad), -speed_m_s * math.cos(rad))


def run_assertions(catalog: dict) -> None:
    require(nearly_equal(1.0 * M_S_PER_FT_MIN, 0.00508), "1 ft/min")
    u, v = wind_from_bearing(2.0, 0.0)
    require(nearly_equal(u, 0.0), "east wind from north")
    require(nearly_equal(v, -2.0), "north wind from north")

    gr1 = catalog[101]
    for mlh, expect in ((0.30, 1.0), (0.75, 0.5), (1.20, 0.0)):
        parts, fc = particles_for(gr1, (0.05, 0.07, 0.09, mlh, 0.90))
        require(nearly_equal(fc, expect), f"curing {mlh}")
        tot = sum(p["w"] for p in parts)
        orig = gr1["w1h"] + gr1["w10h"] + gr1["w100h"] + gr1["wlh"] + gr1["wlw"]
        require(abs(tot - orig) <= 1e-12 * max(1.0, orig), "mass")

    fm1 = catalog[1]
    moist_ext = (0.12, 0.07, 0.09, 0.60, 0.90)
    b = local_behavior(fm1, moist_ext)
    require(nearly_equal(b["ir"], 0.0) and nearly_equal(b["r0_m_s"], 0.0), "FM1 extinction 0.12")
    b = local_behavior(fm1, (0.20, 0.07, 0.09, 0.60, 0.90))
    require(nearly_equal(b["ir"], 0.0) and nearly_equal(b["r0_m_s"], 0.0), "FM1 extinction 0.20")

    gx, gy = 0.3, 0.4
    s = math.hypot(gx, gy)
    require(nearly_equal(s, 0.5), "slope tan")
    q = math.sqrt(1.0 + s ** 2)
    e_up = (gx / (s * q), gy / (s * q), s / q)
    e_cross = (-gy / s, gx / s, 0.0)
    require(nearly_equal(sum(a * b for a, b in zip(e_up, e_cross)), 0.0), "orth")
    require(nearly_equal(math.sqrt(sum(v * v for v in e_up)), 1.0), "e_up")
    require(nearly_equal(math.sqrt(sum(v * v for v in e_cross)), 1.0), "e_cross")

    # isotropic surface R=0.1 on plane gx=0.5, gy=0: support east = 0.1/sqrt(1.25)
    a = b = 0.1
    c = 0.0
    h = (1.0 / math.sqrt(1.0 + 0.5 ** 2), 0.0, 0.5 / math.sqrt(1.25))  # lift of (1,0)
    # project ellipse: d=(c*hx,c*hy), A=(a*hx,a*hy)
    d = (0.0, 0.0)
    A = (a * h[0], a * h[1])
    # k from n x h
    n = (-0.5, 0.0, 1.0)
    nn = math.sqrt(sum(v * v for v in n))
    n = tuple(v / nn for v in n)
    k = (
        n[1] * h[2] - n[2] * h[1],
        n[2] * h[0] - n[0] * h[2],
        n[0] * h[1] - n[1] * h[0],
    )
    kn = math.sqrt(sum(v * v for v in k))
    k = tuple(v / kn for v in k)
    Bv = (b * k[0], b * k[1])
    He = hamiltonian(d, A, Bv, (1.0, 0.0))
    Hn = hamiltonian(d, A, Bv, (0.0, 1.0))
    require(nearly_equal(He, 0.1 / math.sqrt(1.25)), f"east support {He}")
    require(nearly_equal(Hn, 0.1), f"north support {Hn}")

    d = A = Bv = (0.0, 0.0)
    require(hamiltonian(d, A, Bv, (1.0, 1.0)) == 0.0, "zero H")
    require(alpha_bounds(d, A, Bv) == (0.0, 0.0), "zero alpha")

    a, b = 0.2, 0.1
    c = math.sqrt(a * a - b * b)
    d = (c, 0.0)
    A = (a, 0.0)
    Bv = (0.0, b)
    Hhead = hamiltonian(d, A, Bv, (1.0, 0.0))
    Hback = hamiltonian(d, A, Bv, (-1.0, 0.0))
    Hn = hamiltonian(d, A, Bv, (0.0, 1.0))
    require(Hhead > 0 and Hback > 0, "head/back")
    require(nearly_equal(hamiltonian(d, A, Bv, (2.0, 0.0)), 2.0 * Hhead), "homogeneous")
    require(nearly_equal(Hn, b), "normal north")
    e = math.sqrt(1.0 - 1.0 / ((a / b) ** 2))
    r_radial_north = (a + c) * (1.0 - e) / (1.0 - e * math.cos(math.pi / 2.0))
    require(nearly_equal(r_radial_north, (b * b) / a), "radial north")
    require(not nearly_equal(Hn, r_radial_north), "normal != radial")

    ax = 0.2
    dx = 10.0
    cfl = 0.4
    dt = cfl / (ax / dx)
    require(nearly_equal(dt, 20.0), "CFL")


def serialize_case(name: str, model: dict, moisture, **kwargs) -> dict:
    b = local_behavior(model, moisture, **kwargs)
    inter = b["intermediates"]

    def life_map(m):
        return {k: float(v) for k, v in m.items()}

    return {
        "name": name,
        "fuel_id": model["fuel_id"],
        "code": model["code"],
        "moisture": list(moisture),
        "cured_fraction": b["cured_fraction"],
        "sigma": b["sigma"],
        "beta": b["beta"],
        "r_beta": b["r_beta"],
        "gamma": b["gamma"],
        "ir": b["ir"],
        "h_sink": b["h_sink"],
        "r0_m_s": b["r0_m_s"],
        "r_head_m_s": b["r_head_m_s"],
        "r_back_m_s": b["r_back_m_s"],
        "lb": b["lb"],
        "a": b["a"],
        "b": b["b"],
        "c": b["c"],
        "phi_w": b["phi_w"],
        "phi_s": b["phi_s"],
        "phi_eff": b["phi_eff"],
        "u_eff_ft_min": b["u_eff_ft_min"],
        "wind_limited": b["wind_limited"],
        "Wn": life_map(inter.get("Wn", {})),
        "Mbar": life_map(inter.get("Mbar", {})),
        "eta_M": life_map(inter.get("eta_M", {})),
        "eta_S": life_map(inter.get("eta_S", {})),
        "F_l": life_map(inter.get("F_l", {})),
        "IR_l": life_map(inter.get("IR_l", {})),
        "Mx_l": inter.get("Mx_l", 0.0),
        "rho_b": inter.get("rho_b", 0.0),
        "beta_op": inter.get("beta_op", 0.0),
        "xi": inter.get("xi", b.get("xi", 0.0)),
        "C": inter.get("C", 0.0),
        "B": inter.get("B", 0.0),
        "E": inter.get("E", 0.0),
    }


def main() -> None:
    catalog = load_catalog()
    run_assertions(catalog)
    moist = (0.05, 0.07, 0.09, 0.60, 0.90)
    mixed = (0.05, 0.07, 0.09, 0.60, 0.90)
    cases = [
        serialize_case("FM1_nowind", catalog[1], moist),
        serialize_case("FM2_nowind", catalog[2], mixed),
        serialize_case("FM4_nowind", catalog[4], mixed),
        serialize_case("FM1_wind1", catalog[1], moist, wind_east_m_s=1.0),
        serialize_case("FM1_extinction_012", catalog[1], (0.12, 0.07, 0.09, 0.60, 0.90)),
        serialize_case("FM1_extinction_020", catalog[1], (0.20, 0.07, 0.09, 0.60, 0.90)),
        serialize_case("GR1_mlh_030", catalog[101], (0.05, 0.07, 0.09, 0.30, 0.90)),
        serialize_case("GR1_mlh_075", catalog[101], (0.05, 0.07, 0.09, 0.75, 0.90)),
        serialize_case("GR1_mlh_120", catalog[101], (0.05, 0.07, 0.09, 1.20, 0.90)),
        serialize_case("FM10_nowind", catalog[10], moist),
        serialize_case("FM1_slope", catalog[1], moist, gx=math.tan(math.radians(15.0))),
        serialize_case("FM2_wind3", catalog[2], mixed, wind_east_m_s=3.0),
        serialize_case("FM4_wind1_slope15", catalog[4], mixed, wind_east_m_s=1.0, gx=math.tan(math.radians(15.0))),
        serialize_case("FM1_wind_limit_none", catalog[1], moist, wind_east_m_s=1.0, wind_limit="none"),
        serialize_case("FM3_nowind", catalog[3], moist),
        serialize_case("FM5_nowind", catalog[5], mixed),
        serialize_case("FM8_nowind", catalog[8], moist),
    ]
    expected = {
        "FM1": {
            "sigma": 3500,
            "beta": 0.0010625,
            "gamma": 14.201359906473266,
            "ir": 842.6651820057328,
            "h_sink": 9.995229885125415,
            "r0_m_s": 0.024733996158492002,
        },
        "FM2": {
            "sigma": 2784.016729706139,
            "beta": 0.00575,
            "gamma": 15.897033163076461,
            "ir": 3625.4191568692754,
            "h_sink": 64.01525474217043,
            "r0_m_s": 0.015289511177370011,
        },
        "FM4": {
            "sigma": 1739.2378306290284,
            "beta": 0.003833333333333333,
            "gamma": 14.155579218905377,
            "ir": 12362.7718237753,
            "h_sink": 78.22245472881315,
            "r0_m_s": 0.025859989270422667,
        },
    }
    by_name = {c["name"]: c for c in cases}
    for key, exp in expected.items():
        got = by_name[f"{key}_nowind"]
        for field, val in exp.items():
            require(nearly_equal(got[field], val), f"{key} {field}: {got[field]} vs {val}")
    wind = by_name["FM1_wind1"]
    require(nearly_equal(wind["r_head_m_s"], 0.12490103558085179), "FM1 head")
    require(nearly_equal(wind["lb"], 1.207692585557591), "FM1 LB")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "atol": ATOL,
        "rtol": RTOL,
        "units": {
            "ft_per_m": FT_PER_M,
            "m_s_per_ft_min": M_S_PER_FT_MIN,
            "kw_m2_per_btu_ft2_min": KW_M2_PER_BTU_FT2_MIN,
        },
        "cases": cases,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2))
    print(f"wrote {len(cases)} cases to {OUT_JSON}")


if __name__ == "__main__":
    main()
