use fire_types::{
    EnsembleOutput, RunOutput, ARRIVAL_INITIAL_UNKNOWN, ARRIVAL_MODELED, ARRIVAL_NOT_REACHED,
};

pub fn nearest_rank(sorted: &[f64], q: f64) -> Option<f64> {
    if sorted.is_empty() {
        return None;
    }
    let n = sorted.len() as f64;
    let idx = ((q * n).ceil() as isize - 1).max(0) as usize;
    Some(sorted[idx.min(sorted.len() - 1)])
}

pub fn aggregate(members: &[Option<RunOutput>], preset: String, requested: u32) -> EnsembleOutput {
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
    let mut probability = vec![vec![f64::NAN; n]; horizons.len()];
    let mut p_no_arrival = vec![f64::NAN; n];
    let mut q10 = vec![f64::NAN; n];
    let mut q50 = vec![f64::NAN; n];
    let mut q90 = vec![f64::NAN; n];
    if n_complete > 0 && n > 0 {
        let nc = n_complete as f64;
        let max_h = horizons.iter().copied().fold(0.0, f64::max);
        for i in 0..n {
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
    EnsembleOutput {
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
    }
}
