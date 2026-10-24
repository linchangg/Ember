fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    let branch = std::process::Command::new("git")
        .args(["symbolic-ref", "-q", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    let mut names = vec!["HEAD".to_string(), "packed-refs".to_string()];
    names.extend(branch);
    for name in &names {
        if let Ok(path) = std::process::Command::new("git")
            .args(["rev-parse", "--path-format=absolute", "--git-path", name])
            .output()
        {
            if path.status.success() {
                println!(
                    "cargo:rerun-if-changed={}",
                    String::from_utf8_lossy(&path.stdout).trim()
                );
            }
        }
    }
    if let Ok(output) = std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
    {
        if output.status.success() {
            let commit = String::from_utf8_lossy(&output.stdout).trim().to_string();
            println!("cargo:rustc-env=GIT_COMMIT={commit}");
        }
    }
    if let Ok(output) = std::process::Command::new("rustc").arg("-vV").output() {
        if output.status.success() {
            let text = String::from_utf8_lossy(&output.stdout);
            for line in text.lines() {
                if let Some(rest) = line.strip_prefix("release: ") {
                    println!("cargo:rustc-env=RUSTC_RELEASE={rest}");
                }
                if let Some(rest) = line.strip_prefix("host: ") {
                    println!("cargo:rustc-env=RUSTC_HOST={rest}");
                }
            }
        }
    }
}
