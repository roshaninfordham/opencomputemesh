//! Scheduler scoring and property tests.

use ocm_sched::*;
use proptest::prelude::*;

fn node(id: NodeId, tps: Option<f64>, queued: u64) -> NodeSnapshot {
    let mut stat = ModelStat {
        digest: "d".into(),
        loaded: true,
        tps: Ewma::new(0.3),
        ttft_ms: Ewma::new(0.3),
    };
    if let Some(t) = tps {
        stat.tps.observe(t);
        stat.ttft_ms.observe(100.0);
    }
    NodeSnapshot {
        id,
        online: true,
        sharing: true,
        breaker_open: false,
        reputation: 1.0,
        slots: 4,
        active: 0,
        queued_tokens: queued,
        models: vec![stat],
    }
}

fn req() -> Request<'static> {
    Request {
        digest: "d",
        est_tokens: 100,
        exclude: &[],
        pin: None,
    }
}

#[test]
fn ewma_tracks_and_ignores_garbage() {
    let mut e = Ewma::new(0.5);
    assert_eq!(e.get(), None);
    e.observe(10.0);
    e.observe(20.0);
    assert_eq!(e.get(), Some(15.0));
    for bad in [f64::NAN, f64::INFINITY, -1.0] {
        e.observe(bad);
    }
    assert_eq!(e.get(), Some(15.0));
}

#[test]
fn eligibility_rules() {
    let p = Policy::default();
    let ok = node(1, Some(50.0), 0);
    assert!(eligible(&ok, &req(), &p).is_some());
    let mut n = ok.clone();
    n.online = false;
    assert!(eligible(&n, &req(), &p).is_none());
    let mut n = ok.clone();
    n.sharing = false;
    assert!(eligible(&n, &req(), &p).is_none());
    let mut n = ok.clone();
    n.breaker_open = true;
    assert!(eligible(&n, &req(), &p).is_none());
    let mut n = ok.clone();
    n.reputation = 0.1;
    assert!(eligible(&n, &req(), &p).is_none());
    let mut n = ok.clone();
    n.active = n.slots;
    assert!(eligible(&n, &req(), &p).is_none());
    assert!(
        eligible(
            &ok,
            &Request {
                digest: "other",
                ..req()
            },
            &p
        )
        .is_none()
    );
    assert!(
        eligible(
            &ok,
            &Request {
                exclude: &[1],
                ..req()
            },
            &p
        )
        .is_none()
    );
    assert!(
        eligible(
            &ok,
            &Request {
                pin: Some(2),
                ..req()
            },
            &p
        )
        .is_none()
    );
    assert!(
        eligible(
            &ok,
            &Request {
                pin: Some(1),
                ..req()
            },
            &p
        )
        .is_some()
    );
}

#[test]
fn unknown_and_garbage_throughput_use_the_prior() {
    let p = Policy::default();
    let fresh = node(1, None, 0);
    let s = expected_ms(&fresh, &fresh.models[0], &req(), &p);
    assert!(s.is_finite() && s > 0.0);
    let mut zero = node(2, None, 0);
    zero.models[0].tps.observe(0.0);
    let z = expected_ms(&zero, &zero.models[0], &req(), &p);
    assert!(z.is_finite());
}

#[test]
fn cold_models_cost_more() {
    let p = Policy::default();
    let warm = node(1, Some(50.0), 0);
    let mut cold = warm.clone();
    cold.models[0].loaded = false;
    assert!(
        expected_ms(&cold, &cold.models[0], &req(), &p) > expected_ms(&warm, &warm.models[0], &req(), &p)
    );
}

proptest! {
    #[test]
    fn score_is_monotonic_in_queued_tokens(tps in 1.0f64..500.0, q in 0u64..1_000_000, extra in 1u64..100_000) {
        let p = Policy::default();
        let a = node(1, Some(tps), q);
        let b = node(1, Some(tps), q + extra);
        prop_assert!(expected_ms(&b, &b.models[0], &req(), &p) > expected_ms(&a, &a.models[0], &req(), &p));
    }

    #[test]
    fn rng_below_stays_in_range(seed: u64, n in 1usize..10_000) {
        let mut r = ocm_sched::Rng::new(seed);
        for _ in 0..100 { prop_assert!(r.below(n) < n); }
    }
}
