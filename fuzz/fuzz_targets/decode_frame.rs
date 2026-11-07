#![no_main]

use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if let Ok(frame) = ocm_proto::Frame::decode(data) {
        let again = ocm_proto::Frame::decode(&frame.encode()).expect("re-encoding a decoded frame must decode");
        assert_eq!(again, frame);
        let _ = ocm_proto::verify(&frame, &"00".repeat(32));
    }
});
