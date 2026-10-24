#![forbid(unsafe_code)]

pub mod arrays;
pub mod export;
pub mod manifest;

pub use arrays::{sha256_file, sha256_hex, write_atomic, write_f64le};
pub use export::{export_run, horizon_from_state, make_fm1_circle_fixture, run_identifier};
pub use manifest::load_run_bundle;

#[cfg(test)]
mod tests {
    use super::*;
    use fire_propagation::validate_inputs;

    #[test]
    fn fixture_roundtrip_validation() {
        let dir = std::env::temp_dir().join(format!("fm1-{}", std::process::id()));
        make_fm1_circle_fixture(&dir).unwrap();
        let input = load_run_bundle(&dir.join("manifest.json"), 512 * 1024 * 1024).unwrap();
        validate_inputs(&input).unwrap();
        assert_eq!(input.manifest.grid.width, 64);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn corrupt_length_rejected() {
        let dir = std::env::temp_dir().join(format!("fm1-bad-{}", std::process::id()));
        make_fm1_circle_fixture(&dir).unwrap();
        std::fs::write(dir.join("valid.u8"), [1u8, 1, 1]).unwrap();
        let err = load_run_bundle(&dir.join("manifest.json"), 512 * 1024 * 1024).unwrap_err();
        assert_eq!(err.code, fire_types::ErrorCode::InvalidArrayLength);
        std::fs::remove_dir_all(&dir).ok();
    }
}
