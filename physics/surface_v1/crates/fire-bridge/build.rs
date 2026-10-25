use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
};
fn sources(dir: &Path, output: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(dir)
        .expect("source directory")
        .map(Result::unwrap)
    {
        let path = entry.path();
        if path.is_dir() {
            sources(&path, output)
        } else if path.extension().is_some_and(|v| v == "rs") {
            output.push(path);
        }
    }
}
fn main() {
    let root = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap())
        .join("../..")
        .canonicalize()
        .unwrap();
    let mut files = vec![
        root.join("Cargo.toml"),
        root.join("Cargo.lock"),
        root.join("rust-toolchain.toml"),
    ];
    for name in [
        "fire-types",
        "fire-behavior",
        "fire-propagation",
        "fire-io",
        "fire-bridge",
    ] {
        let dir = root.join("crates").join(name);
        files.push(dir.join("Cargo.toml"));
        if dir.join("build.rs").is_file() {
            files.push(dir.join("build.rs"));
        }
        sources(&dir.join("src"), &mut files);
    }
    files.sort();
    let mut hash = Sha256::new();
    for file in files {
        println!("cargo:rerun-if-changed={}", file.display());
        let relative = file
            .strip_prefix(&root)
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/");
        hash.update(relative.as_bytes());
        hash.update([0]);
        hash.update(fs::read(file).unwrap());
        hash.update([0]);
    }
    println!("cargo:rustc-env=FIRE_PHYSICS_SHA256={:x}", hash.finalize());
}
