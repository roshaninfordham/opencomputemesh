#![no_main]

use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if let Ok(text) = std::str::from_utf8(data) {
        // The contract is total: arbitrary bytes produce an error list, never a
        // panic, an infinite loop or unbounded recursion.
        let _ = ocm_config::test_config(text);
    }
});
