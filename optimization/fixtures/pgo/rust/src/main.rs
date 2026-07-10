use std::env;
use std::process::ExitCode;

fn parse_positive(name: &str, value: Option<String>) -> Result<u64, String> {
    let raw = value.ok_or_else(|| format!("missing {name}"))?;
    let parsed = raw
        .parse::<u64>()
        .map_err(|error| format!("invalid {name} {raw:?}: {error}"))?;
    if parsed == 0 {
        return Err(format!("{name} must be positive"));
    }
    Ok(parsed)
}

fn main() -> ExitCode {
    let mut arguments = env::args().skip(1);
    let mode = match parse_positive("mode", arguments.next()) {
        Ok(value) => value,
        Err(error) => {
            eprintln!("usage: rust-pgo-fixture MODE ITERATIONS\n{error}");
            return ExitCode::from(2);
        }
    };
    let iterations = match parse_positive("iterations", arguments.next()) {
        Ok(value) => value,
        Err(error) => {
            eprintln!("usage: rust-pgo-fixture MODE ITERATIONS\n{error}");
            return ExitCode::from(2);
        }
    };
    if arguments.next().is_some() {
        eprintln!("usage: rust-pgo-fixture MODE ITERATIONS\nunexpected argument");
        return ExitCode::from(2);
    }

    let checksum = rust_pgo_fixture::run(mode, iterations);
    #[cfg(feature = "profile-mismatch")]
    let checksum = rust_pgo_fixture::never_trained_profile_mismatch(checksum);
    println!(
        "mode={mode} iterations={iterations} checksum={checksum:016x} sentinel={:016x}",
        rust_pgo_fixture::BUILD_SENTINEL
    );
    ExitCode::SUCCESS
}
