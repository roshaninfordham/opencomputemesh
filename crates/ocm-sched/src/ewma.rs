//! Exponentially weighted moving average of measured performance.

/// Moving average; `None` until the first valid sample.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Ewma {
    value: Option<f64>,
    alpha: f64,
}

impl Ewma {
    /// `alpha` in (0, 1]: weight of the newest sample.
    pub const fn new(alpha: f64) -> Self {
        Self { value: None, alpha }
    }

    /// Add a sample; non-finite and negative samples are ignored.
    pub fn observe(&mut self, x: f64) {
        if !x.is_finite() || x < 0.0 {
            return;
        }
        self.value = Some(self.value.map_or(x, |v| v + self.alpha * (x - v)));
    }

    /// Current average.
    pub fn get(&self) -> Option<f64> {
        self.value
    }
}
