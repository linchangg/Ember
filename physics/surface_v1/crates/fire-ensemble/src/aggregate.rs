use fire_types::{
    EngineError, EngineResult, EnsembleOutput, ErrorCode, RunOutput, ARRIVAL_INITIAL_UNKNOWN,
    ARRIVAL_INVALID, ARRIVAL_MODELED, ARRIVAL_NOT_REACHED,
};

pub fn nearest_rank(sorted: &[f64], q: f64) -> Option<f64> {
    if sorted.is_empty() {
        return None;
    }
    let n = sorted.len() as f64;
    let idx = ((q * n).ceil() as isize - 1).max(0) as usize;
    Some(sorted[idx.min(sorted.len() - 1)])
}

pub fn aggregate(
    members: &[Option<RunOutput>],
    preset: String,
    requested: u32,
) -> EngineResult<EnsembleOutput> {
    let complete: Vec<&RunOutput> = members.iter().filter_map(|m| m.as_ref()).collect();
    let n_complete = complete.len() as u32;
    let n = complete
        .first()
        .map(|r| r.arrival_status.len())
        .unwrap_or(0);
    let horizons: Vec<f64> = complete
        .first()
        .map(|r| r.run.horizons_s.clone())
        .unwrap_or_default();
    if let Some(first) = complete.first() {
        for member in &complete {
            if member.grid != first.grid
                || member.run.horizons_s != horizons
                || member.arrival_status.len() != n
                || member.arrival_time_s.len() != n
                || member.run.status != "complete"
                || member.run.start_utc != first.run.start_utc
            {
                return Err(EngineError::new(
                    ErrorCode::SchemaMismatch,
                    "ensemble grids, completion, origins, horizons, or array shapes differ",
                ));
            }
            if member
                .arrival_status
                .iter()
                .zip(&member.arrival_time_s)
                .any(|(status, t)| {
                    *status > ARRIVAL_MODELED
                        || (*status == ARRIVAL_MODELED
                            && (!t.is_finite() || *t < 0.0 || *t > member.run.completed_until_s))
                })
            {
                return Err(EngineError::new(
                    ErrorCode::SchemaMismatch,
                    "invalid ensemble arrival",
                ));
            }
        }
    }
    let mut probability = vec![vec![f64::NAN; n]; horizons.len()];
    let mut p_no_arrival = vec![f64::NAN; n];
    let mut q10 = vec![f64::NAN; n];
    let mut q50 = vec![f64::NAN; n];
    let mut q90 = vec![f64::NAN; n];
    let mut valid = vec![0u8; n];
    let mut member_coverage = vec![0u32; n];
    if n_complete > 0 && n > 0 {
        let nc = n_complete as f64;
        let max_h = horizons.iter().copied().fold(0.0, f64::max);
        for i in 0..n {
            let covered = complete
                .iter()
                .filter(|m| m.arrival_status[i] != ARRIVAL_INVALID)
                .count() as u32;
            member_coverage[i] = covered;
            if covered != n_complete {
                continue;
            }
            valid[i] = 1;
            let mut no_arr = 0.0;
            let mut arrivals = Vec::new();
            for m in &complete {
                let st = m.arrival_status[i];
                if st == ARRIVAL_NOT_REACHED {
                    no_arr += 1.0;
                }
                if st == ARRIVAL_MODELED && m.arrival_time_s[i] <= max_h {
                    arrivals.push(m.arrival_time_s[i]);
                }
            }
            p_no_arrival[i] = no_arr / nc;
            arrivals.sort_by(|a, b| a.partial_cmp(b).unwrap());
            if let Some(v) = nearest_rank(&arrivals, 0.1) {
                q10[i] = v;
            }
            if let Some(v) = nearest_rank(&arrivals, 0.5) {
                q50[i] = v;
            }
            if let Some(v) = nearest_rank(&arrivals, 0.9) {
                q90[i] = v;
            }
            for (hi, h) in horizons.iter().enumerate() {
                let mut c = 0.0;
                for m in &complete {
                    let burned = m.arrival_status[i] == ARRIVAL_INITIAL_UNKNOWN
                        || (m.arrival_status[i] == ARRIVAL_MODELED && m.arrival_time_s[i] <= *h);
                    if burned {
                        c += 1.0;
                    }
                }
                probability[hi][i] = c / nc;
            }
        }
    }
    Ok(EnsembleOutput {
        grid: complete.first().map(|m| m.grid.clone()),
        horizons_s: horizons,
        valid,
        member_coverage,
        preset,
        n_requested: requested,
        n_complete,
        partial: n_complete != requested,
        members: Vec::new(),
        probability,
        p_no_arrival,
        arrival_q10: q10,
        arrival_q50: q50,
        arrival_q90: q90,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn member(status: u8, time: f64) -> RunOutput {
        let grid = fire_types::GridSpec {
            width: 3,
            height: 3,
            cell_size_m: 10.0,
            x0_m: 0.0,
            y0_m: 0.0,
            crs: "LOCAL_METRIC_TEST".into(),
        };
        RunOutput {
            run: fire_types::RunJson {
                schema_version: 2,
                status: "complete".into(),
                model: "surface_v1".into(),
                solver: "level_set_lf_euler_v1".into(),
                incident_id: "test".into(),
                start_utc: "2026-01-01T00:00:00Z".into(),
                completed_until_s: 600.0,
                horizons_s: vec![300.0, 600.0],
                input_manifest_sha256: "test".into(),
                input_artifacts: vec![],
                build: fire_types::BuildInfo::default(),
                configuration: serde_json::json!({}),
                quality_flags: vec![],
                parent_run: None,
            },
            grid,
            arrival_status: vec![status; 9],
            arrival_time_s: vec![time; 9],
            horizons: vec![],
            diagnostics: serde_json::json!({}),
        }
    }
    #[test]
    fn invalid_cells_are_missing_not_zero_probability() {
        let out = aggregate(&[Some(member(ARRIVAL_INVALID, 0.0))], "test".into(), 1).unwrap();
        assert_eq!(out.valid[0], 0);
        assert_eq!(out.member_coverage[0], 0);
        assert!(out.probability[0][0].is_nan());
        assert!(out.p_no_arrival[0].is_nan());
    }
    #[test]
    fn probabilities_and_conditional_quantiles_have_distinct_denominators() {
        let out = aggregate(
            &[
                Some(member(ARRIVAL_MODELED, 100.0)),
                Some(member(ARRIVAL_MODELED, 500.0)),
                Some(member(ARRIVAL_NOT_REACHED, 0.0)),
                Some(member(ARRIVAL_INITIAL_UNKNOWN, 0.0)),
                None,
            ],
            "test".into(),
            5,
        )
        .unwrap();
        assert_eq!(out.n_complete, 4);
        assert!(out.partial);
        assert_eq!(out.probability[0][0], 0.5);
        assert_eq!(out.probability[1][0], 0.75);
        assert_eq!(out.p_no_arrival[0], 0.25);
        assert_eq!(out.arrival_q10[0], 100.0);
        assert_eq!(out.arrival_q50[0], 100.0);
        assert_eq!(out.arrival_q90[0], 500.0);
    }
    #[test]
    fn mixed_invalid_coverage_and_inconsistent_members_rejected_or_missing() {
        let out = aggregate(
            &[
                Some(member(ARRIVAL_INVALID, 0.0)),
                Some(member(ARRIVAL_NOT_REACHED, 0.0)),
            ],
            "test".into(),
            2,
        )
        .unwrap();
        assert_eq!(out.member_coverage[0], 1);
        assert_eq!(out.valid[0], 0);
        let mut bad = member(ARRIVAL_MODELED, 100.0);
        bad.arrival_time_s.clear();
        assert!(aggregate(&[Some(bad)], "test".into(), 1).is_err());
    }
}
