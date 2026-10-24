use std::collections::BTreeMap;
use std::str::FromStr;

use fire_types::{EngineError, EngineResult, ErrorCode, Moisture};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LifeState {
    Dead,
    Live,
}

#[derive(Debug, Clone)]
pub struct Particle {
    pub life: LifeState,
    pub name: String,
    pub w: f64,
    pub sigma: f64,
    pub rho_p: f64,
    pub h: f64,
    pub s_t: f64,
    pub s_e: f64,
    pub m: f64,
}

#[derive(Debug, Clone)]
pub struct FuelModel {
    pub fuel_id: u16,
    pub code: String,
    pub name: String,
    pub is_dynamic: bool,
    pub depth_ft: f64,
    pub mx_dead: f64,
    pub heat_dead: f64,
    pub heat_live: f64,
    pub w1h: f64,
    pub w10h: f64,
    pub w100h: f64,
    pub wlh: f64,
    pub wlw: f64,
    pub sav1h: f64,
    pub savlh: f64,
    pub savlw: f64,
    pub rho_p: f64,
    pub s_t: f64,
    pub s_e: f64,
    pub sav10h: f64,
    pub sav100h: f64,
}

impl FuelModel {
    pub fn original_load(&self) -> f64 {
        self.w1h + self.w10h + self.w100h + self.wlh + self.wlw
    }
}

#[derive(Debug, Clone, Default)]
pub struct FuelCatalog {
    by_id: BTreeMap<u16, FuelModel>,
    by_code: BTreeMap<String, u16>,
}

impl FuelCatalog {
    pub fn parse_csv(text: &str) -> EngineResult<Self> {
        let mut lines = text.lines();
        let header = lines
            .next()
            .ok_or_else(|| EngineError::new(ErrorCode::SchemaMismatch, "fuel catalog is empty"))?;
        let cols = parse_csv_line(header);
        let idx = |name: &str| -> EngineResult<usize> {
            cols.iter().position(|c| c == name).ok_or_else(|| {
                EngineError::new(
                    ErrorCode::SchemaMismatch,
                    format!("fuel catalog missing column {name}"),
                )
            })
        };
        let i_id = idx("fuel_id")?;
        let i_code = idx("code")?;
        let i_name = idx("name")?;
        let i_dyn = idx("is_dynamic")?;
        let i_depth = idx("depth_ft")?;
        let i_mx = idx("mx_dead")?;
        let i_hd = idx("heat_dead_btu_lb")?;
        let i_hl = idx("heat_live_btu_lb")?;
        let i_w1 = idx("w1h_lb_ft2")?;
        let i_w10 = idx("w10h_lb_ft2")?;
        let i_w100 = idx("w100h_lb_ft2")?;
        let i_wlh = idx("wlh_lb_ft2")?;
        let i_wlw = idx("wlw_lb_ft2")?;
        let i_s1 = idx("sav1h_per_ft")?;
        let i_slh = idx("savlh_per_ft")?;
        let i_slw = idx("savlw_per_ft")?;
        let i_rho = idx("rho_p_lb_ft3")?;
        let i_st = idx("s_t")?;
        let i_se = idx("s_e")?;
        let i_s10 = idx("sav10h_per_ft")?;
        let i_s100 = idx("sav100h_per_ft")?;

        let mut cat = FuelCatalog::default();
        for (lineno, line) in lines.enumerate() {
            if line.trim().is_empty() {
                continue;
            }
            let row = parse_csv_line(line);
            let get = |i: usize| -> EngineResult<&str> {
                row.get(i).map(|s| s.as_str()).ok_or_else(|| {
                    EngineError::new(
                        ErrorCode::SchemaMismatch,
                        format!("fuel catalog short row at line {}", lineno + 2),
                    )
                })
            };
            let parse_f = |i: usize, name: &str| -> EngineResult<f64> {
                let s = get(i)?;
                f64::from_str(s).map_err(|_| {
                    EngineError::new(
                        ErrorCode::InvalidFuelParameter,
                        format!("cannot parse {name}={s}"),
                    )
                })
            };
            let model = FuelModel {
                fuel_id: get(i_id)?
                    .parse()
                    .map_err(|_| EngineError::new(ErrorCode::SchemaMismatch, "invalid fuel_id"))?,
                code: get(i_code)?.to_string(),
                name: get(i_name)?.to_string(),
                is_dynamic: matches!(get(i_dyn)?, "1" | "true" | "True"),
                depth_ft: parse_f(i_depth, "depth_ft")?,
                mx_dead: parse_f(i_mx, "mx_dead")?,
                heat_dead: parse_f(i_hd, "heat_dead")?,
                heat_live: parse_f(i_hl, "heat_live")?,
                w1h: parse_f(i_w1, "w1h")?,
                w10h: parse_f(i_w10, "w10h")?,
                w100h: parse_f(i_w100, "w100h")?,
                wlh: parse_f(i_wlh, "wlh")?,
                wlw: parse_f(i_wlw, "wlw")?,
                sav1h: parse_f(i_s1, "sav1h")?,
                savlh: parse_f(i_slh, "savlh")?,
                savlw: parse_f(i_slw, "savlw")?,
                rho_p: parse_f(i_rho, "rho_p")?,
                s_t: parse_f(i_st, "s_t")?,
                s_e: parse_f(i_se, "s_e")?,
                sav10h: parse_f(i_s10, "sav10h")?,
                sav100h: parse_f(i_s100, "sav100h")?,
            };
            if cat.by_id.contains_key(&model.fuel_id) {
                return Err(EngineError::new(
                    ErrorCode::SchemaMismatch,
                    format!("duplicate fuel_id {}", model.fuel_id),
                ));
            }
            cat.by_code.insert(model.code.clone(), model.fuel_id);
            cat.by_id.insert(model.fuel_id, model);
        }
        Ok(cat)
    }

    pub fn get(&self, id: u16) -> EngineResult<&FuelModel> {
        self.by_id.get(&id).ok_or_else(|| {
            EngineError::new(ErrorCode::UnsupportedFuel, format!("unknown fuel id {id}"))
        })
    }

    pub fn get_code(&self, code: &str) -> EngineResult<&FuelModel> {
        let id = self.by_code.get(code).ok_or_else(|| {
            EngineError::new(
                ErrorCode::UnsupportedFuel,
                format!("unknown fuel code {code}"),
            )
        })?;
        self.get(*id)
    }

    pub fn ids(&self) -> impl Iterator<Item = u16> + '_ {
        self.by_id.keys().copied()
    }
}

pub fn curing_fraction(m_lh: f64) -> f64 {
    ((1.20 - m_lh) / 0.90).clamp(0.0, 1.0)
}

pub fn particles_for(model: &FuelModel, moisture: Moisture) -> EngineResult<(Vec<Particle>, f64)> {
    moisture.validate()?;
    let (fc, w_cured, w_lh) = if model.is_dynamic {
        let fc = curing_fraction(moisture.mlh);
        (fc, fc * model.wlh, (1.0 - fc) * model.wlh)
    } else {
        (0.0, 0.0, model.wlh)
    };
    let specs = [
        (
            LifeState::Dead,
            "1h",
            model.w1h,
            model.sav1h,
            moisture.m1,
            model.heat_dead,
        ),
        (
            LifeState::Dead,
            "10h",
            model.w10h,
            model.sav10h,
            moisture.m10,
            model.heat_dead,
        ),
        (
            LifeState::Dead,
            "100h",
            model.w100h,
            model.sav100h,
            moisture.m100,
            model.heat_dead,
        ),
        (
            LifeState::Dead,
            "cured_herb",
            w_cured,
            model.savlh,
            moisture.m1,
            model.heat_dead,
        ),
        (
            LifeState::Live,
            "herb",
            w_lh,
            model.savlh,
            moisture.mlh,
            model.heat_live,
        ),
        (
            LifeState::Live,
            "woody",
            model.wlw,
            model.savlw,
            moisture.mlw,
            model.heat_live,
        ),
    ];
    let mut parts = Vec::new();
    for (life, name, w, sigma, m, h) in specs {
        if w <= 0.0 {
            continue;
        }
        parts.push(Particle {
            life,
            name: name.to_string(),
            w,
            sigma,
            rho_p: model.rho_p,
            h,
            s_t: model.s_t,
            s_e: model.s_e,
            m,
        });
    }
    let transferred: f64 = parts.iter().map(|p| p.w).sum();
    let orig = model.original_load();
    if orig > 0.0 {
        let rel = (transferred - orig).abs() / orig.max(1.0);
        if (transferred - orig).abs() > 1e-12 * orig.max(1.0) && rel > 1e-12 {
            return Err(EngineError::new(
                ErrorCode::InvalidFuelParameter,
                format!("load not conserved: orig={orig} transferred={transferred}"),
            ));
        }
    }
    Ok((parts, fc))
}

fn parse_csv_line(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut quoted = false;
    let mut chars = line.chars().peekable();
    while let Some(ch) = chars.next() {
        match ch {
            '"' => {
                if quoted && chars.peek() == Some(&'"') {
                    chars.next();
                    cur.push('"');
                } else {
                    quoted = !quoted;
                }
            }
            ',' if !quoted => {
                out.push(cur.trim().to_string());
                cur.clear();
            }
            _ => cur.push(ch),
        }
    }
    out.push(cur.trim().to_string());
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use fire_types::fixture_close;

    const CATALOG: &str = include_str!("../../../docs/spec/fuel_models.csv");

    #[test]
    fn catalog_loads_required_ids() {
        let cat = FuelCatalog::parse_csv(CATALOG).unwrap();
        for id in 1..=13 {
            cat.get(id).unwrap();
        }
        cat.get(101).unwrap();
        cat.get_code("FM1").unwrap();
        cat.get_code("GR1").unwrap();
    }

    #[test]
    fn curing_endpoints_and_mass() {
        let cat = FuelCatalog::parse_csv(CATALOG).unwrap();
        let gr1 = cat.get(101).unwrap();
        for (mlh, expect) in [(0.30, 1.0), (0.75, 0.5), (1.20, 0.0)] {
            let moist = Moisture {
                m1: 0.05,
                m10: 0.07,
                m100: 0.09,
                mlh,
                mlw: 0.90,
            };
            let (parts, fc) = particles_for(gr1, moist).unwrap();
            assert!(fixture_close(fc, expect));
            let tot: f64 = parts.iter().map(|p| p.w).sum();
            let orig = gr1.original_load();
            assert!((tot - orig).abs() <= 1e-12 * orig.max(1.0));
        }
    }
}
