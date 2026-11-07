//! `SplitMix64`: tiny, fast, seedable; good enough for load balancing, not for cryptography.

/// Deterministic pseudo-random generator.
#[derive(Debug, Clone)]
pub struct Rng(u64);

impl Rng {
    /// A generator from a seed; equal seeds give equal sequences.
    pub fn new(seed: u64) -> Self {
        Self(seed)
    }

    /// Next 64 random bits.
    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform index in `0..n` (`Lemire`'s multiply-shift; unbiased enough for scheduling).
    ///
    /// # Panics
    /// Panics if `n` is zero.
    pub fn below(&mut self, n: usize) -> usize {
        assert!(n > 0, "below(0)");
        let wide = u128::from(self.next_u64()) * (n as u128);
        usize::try_from(wide >> 64).expect("result is below n, which fits in usize")
    }
}
