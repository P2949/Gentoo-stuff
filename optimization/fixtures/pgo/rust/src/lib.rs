include!(concat!(env!("OUT_DIR"), "/build_sentinel.rs"));

#[inline(always)]
fn rotate_mix(value: u64, shift: u32) -> u64 {
    value.rotate_left(shift) ^ value.wrapping_mul(0x9e37_79b9_7f4a_7c15)
}

#[inline(never)]
fn hot_even(mut value: u64, rounds: u64) -> u64 {
    for index in 0..rounds {
        value = rotate_mix(value ^ index, ((index & 31) + 1) as u32);
        if value & 7 == 0 {
            value = value.wrapping_add(0xa076_1d64_78bd_642f);
        }
    }
    value
}

#[inline(never)]
fn hot_odd(mut value: u64, rounds: u64) -> u64 {
    for index in 0..rounds {
        value = rotate_mix(
            value.wrapping_add(index ^ 0xe703_7ed1_a0b4_28db),
            ((index & 15) + 7) as u32,
        );
        if value & 15 == 3 {
            value ^= 0x8ebc_6af0_9c88_c6e3;
        }
    }
    value
}

pub fn run(mode: u64, iterations: u64) -> u64 {
    let mut result = BUILD_SENTINEL ^ mode.wrapping_mul(0xd6e8_feb8_6659_fd93);
    let rounds = iterations / 16 + 1;
    for index in 0..16 {
        result = if (mode + index) & 1 == 0 {
            hot_even(result ^ index, rounds)
        } else {
            hot_odd(result.wrapping_add(index), rounds)
        };
    }
    result
}

#[cfg(feature = "profile-mismatch")]
#[inline(never)]
pub fn never_trained_profile_mismatch(mut value: u64) -> u64 {
    for index in 0_u64..2048 {
        value = value
            .wrapping_mul(0xd134_2543_de82_ef95)
            .rotate_right(((index & 31) + 1) as u32)
            ^ index;
    }
    value
}

#[cfg(test)]
mod tests {
    use super::{BUILD_SENTINEL, run};

    #[test]
    fn workload_is_deterministic_and_mode_sensitive() {
        assert_eq!(run(2, 8_192), run(2, 8_192));
        assert_ne!(run(2, 8_192), run(3, 8_192));
        assert_eq!(BUILD_SENTINEL, 0x5255_5354_5047_4f31);
    }
}
