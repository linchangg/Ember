use fire_eval::binary_metrics;
#[test]
fn false_area_uses_metric_cell_area() {
    let m = binary_metrics(&[1, 1, 0], &[1, 0, 1], &[1, 1, 1], 90.0, 90.0).unwrap();
    assert_eq!(m.false_burn_area, 8100.0);
    assert_eq!(m.iou, Some(1.0 / 3.0));
    assert_eq!(m.dice, Some(0.5));
}
#[test]
fn missing_masks_are_errors_and_empty_coverage_is_missing() {
    assert!(binary_metrics(&[1, 1], &[1, 1], &[], 10.0, 10.0).is_err());
    assert!(binary_metrics(&[1], &[1], &[1], f64::NAN, 10.0).is_err());
    assert_eq!(
        binary_metrics(&[0], &[0], &[0], 10.0, 10.0).unwrap().iou,
        None
    );
}
#[test]
fn probability_and_arrival_metrics_validate_shapes() {
    assert!(fire_eval::brier(&[0.5], &[], &[1]).is_err());
    assert!(fire_eval::brier(&[1.5], &[1], &[1]).is_err());
    assert_eq!(fire_eval::brier(&[0.5], &[1], &[1]).unwrap(), Some(0.25));
    assert!(fire_eval::arrival_mae(&[1.0], &[3], &[], &[1]).is_err());
}
