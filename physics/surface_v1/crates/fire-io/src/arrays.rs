use std::fs;
use std::path::Path;

use fire_types::{EngineError, EngineResult, ErrorCode};
use sha2::{Digest, Sha256};

pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

pub fn sha256_file(path: &Path) -> EngineResult<(String, u64, Vec<u8>)> {
    let bytes = fs::read(path).map_err(|e| {
        EngineError::new(
            ErrorCode::SchemaMismatch,
            format!("cannot read {}: {e}", path.display()),
        )
        .artifact(path.display().to_string())
    })?;
    let n = bytes.len() as u64;
    Ok((sha256_hex(&bytes), n, bytes))
}

pub fn write_atomic(path: &Path, bytes: &[u8]) -> EngineResult<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| {
            EngineError::new(
                ErrorCode::SchemaMismatch,
                format!("mkdir {}: {e}", parent.display()),
            )
        })?;
    }
    let tmp = path.with_extension("partial");
    fs::write(&tmp, bytes).map_err(|e| {
        EngineError::new(
            ErrorCode::SchemaMismatch,
            format!("write {}: {e}", tmp.display()),
        )
    })?;
    fs::rename(&tmp, path).map_err(|e| {
        EngineError::new(
            ErrorCode::SchemaMismatch,
            format!("rename {}: {e}", path.display()),
        )
    })?;
    Ok(())
}

pub fn read_f64le(bytes: &[u8], n: usize, artifact: &str) -> EngineResult<Vec<f64>> {
    let expect = n.checked_mul(8).ok_or_else(|| {
        EngineError::new(ErrorCode::InvalidArrayLength, "f64 length overflow").artifact(artifact)
    })?;
    if bytes.len() != expect {
        return Err(EngineError::new(
            ErrorCode::InvalidArrayLength,
            format!("expected {expect} bytes, got {}", bytes.len()),
        )
        .artifact(artifact));
    }
    let mut out = Vec::with_capacity(n);
    for chunk in bytes.as_chunks::<8>().0 {
        out.push(f64::from_le_bytes(*chunk));
    }
    Ok(out)
}

pub fn read_u16le(bytes: &[u8], n: usize, artifact: &str) -> EngineResult<Vec<u16>> {
    let expect = n.checked_mul(2).ok_or_else(|| {
        EngineError::new(ErrorCode::InvalidArrayLength, "u16 length overflow").artifact(artifact)
    })?;
    if bytes.len() != expect {
        return Err(EngineError::new(
            ErrorCode::InvalidArrayLength,
            format!("expected {expect} bytes, got {}", bytes.len()),
        )
        .artifact(artifact));
    }
    Ok(bytes
        .as_chunks::<2>()
        .0
        .iter()
        .map(|c| u16::from_le_bytes(*c))
        .collect())
}

pub fn read_u8(bytes: &[u8], n: usize, artifact: &str) -> EngineResult<Vec<u8>> {
    if bytes.len() != n {
        return Err(EngineError::new(
            ErrorCode::InvalidArrayLength,
            format!("expected {n} bytes, got {}", bytes.len()),
        )
        .artifact(artifact));
    }
    Ok(bytes.to_vec())
}

pub fn write_f64le(vals: &[f64]) -> Vec<u8> {
    let mut b = Vec::with_capacity(vals.len() * 8);
    for v in vals {
        b.extend_from_slice(&v.to_le_bytes());
    }
    b
}

pub fn write_u16le(vals: &[u16]) -> Vec<u8> {
    let mut b = Vec::with_capacity(vals.len() * 2);
    for v in vals {
        b.extend_from_slice(&v.to_le_bytes());
    }
    b
}

pub fn confined_path(root: &Path, relative: &str) -> EngineResult<std::path::PathBuf> {
    if relative.is_empty()
        || Path::new(relative).is_absolute()
        || relative.split(['/', '\\']).any(|p| p == "..")
    {
        return Err(EngineError::new(
            ErrorCode::SchemaMismatch,
            format!("path escapes bundle: {relative}"),
        ));
    }
    Ok(root.join(relative))
}
