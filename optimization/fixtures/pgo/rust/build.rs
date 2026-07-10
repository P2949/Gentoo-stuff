use std::env;
use std::fs;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo did not set OUT_DIR"));
    fs::write(
        output.join("build_sentinel.rs"),
        "pub const BUILD_SENTINEL: u64 = 0x5255_5354_5047_4f31;\n",
    )
    .expect("failed to write deterministic build sentinel");
}
