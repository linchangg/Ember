//! Deliberately narrow normalized-bundle timestamp format: whole-second UTC.
use crate::{EngineError, EngineResult, ErrorCode};
pub fn utc_seconds(value: &str) -> EngineResult<i64> {
    let invalid = || {
        EngineError::new(
            ErrorCode::SchemaMismatch,
            "expected valid YYYY-MM-DDTHH:MM:SSZ timestamp",
        )
    };
    let b = value.as_bytes();
    if b.len() != 20
        || b[4] != b'-'
        || b[7] != b'-'
        || b[10] != b'T'
        || b[13] != b':'
        || b[16] != b':'
        || b[19] != b'Z'
    {
        return Err(invalid());
    }
    let number = |a: usize, z: usize| -> EngineResult<i64> {
        if !b[a..z].iter().all(u8::is_ascii_digit) {
            return Err(invalid());
        }
        value[a..z].parse().map_err(|_| invalid())
    };
    let year = number(0, 4)?;
    let month = number(5, 7)?;
    let day = number(8, 10)?;
    let hour = number(11, 13)?;
    let minute = number(14, 16)?;
    let second = number(17, 19)?;
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    if year < 1970
        || !(1..=12).contains(&month)
        || day < 1
        || day > days[(month - 1) as usize]
        || hour > 23
        || minute > 59
        || second > 59
    {
        return Err(invalid());
    }
    let before = |y: i64| 365 * (y - 1) + (y - 1) / 4 - (y - 1) / 100 + (y - 1) / 400;
    let d =
        before(year) - before(1970) + days[..(month - 1) as usize].iter().sum::<i64>() + day - 1;
    Ok(d * 86400 + hour * 3600 + minute * 60 + second)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn utc_calendar() {
        assert_eq!(utc_seconds("1970-01-01T00:00:00Z").unwrap(), 0);
        assert_eq!(
            utc_seconds("2024-03-01T00:00:00Z").unwrap()
                - utc_seconds("2024-02-28T00:00:00Z").unwrap(),
            172800
        );
        for s in [
            "2026-02-29T00:00:00Z",
            "2026-01-01T24:00:00Z",
            "2026-01-01T00:00:00+00:00",
        ] {
            assert!(utc_seconds(s).is_err());
        }
    }
}
