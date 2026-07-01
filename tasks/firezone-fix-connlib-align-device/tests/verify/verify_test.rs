//! Behavioural verifier for `firezone-fix-connlib-align-device`.
//!
//! Tests through the PRE-EXISTING public serde wire contract of the
//! connlib data plane's portal message types, living in the `tunnel`
//! crate's `messages::client` module:
//!
//!   * `tunnel::messages::client::ResourceDescriptionStaticDevicePool`
//!     / `DevicePoolMember`
//!   * `tunnel::messages::client::ClientDeviceAccessDenied`
//!   * `tunnel::messages::client::FailReason`
//!
//! This integration test is hosted in the `client-shared` crate, which
//! consumes `tunnel` as a NORMAL dependency — so the message types are
//! reachable WITHOUT compiling `tunnel`'s `[dev-dependencies]` (which
//! drag in `firezone-relay`'s eBPF chain: a nightly toolchain +
//! bpf-linker + LLVM that this image deliberately does not install).
//! `client-shared`'s own dev-deps (`serde_json`, `chrono`, `tokio`)
//! are light.
//!
//! Decoupling rationale for each test's shape:
//!
//!   * Only PRE-EXISTING public message types are imported — nothing
//!     the fix invents. The fix is a set of serde attributes / enum
//!     variants on these existing types; the tests observe the wire
//!     contract those attributes govern, not the attributes
//!     themselves.
//!   * `static_device_pool_decodes_members_with_portal_wire_key` only
//!     uses the portal's `client_id` wire key. It does NOT assert that
//!     the legacy `id` key still works — a `#[serde(rename = "client_id")]`
//!     solution need not keep `id`, so requiring it would wrongly reject a
//!     valid solution. It also reads `member.id` (the stable in-memory field
//!     name) and compares the rendered UUID, not any private field.
//!   * `access_denied_tolerates_missing_addresses` asserts decode
//!     SUCCESS via `.is_ok()` rather than inspecting `.ipv4.is_none()`.
//!     The latter would couple the test to the `Option` representation
//!     and turn the pre-fix state into a COMPILE error instead of a
//!     behavioural failure; `.is_ok()` discriminates by behaviour
//!     (pre-fix yields `Err("missing field ipv4")`).
//!   * `fail_reason_recognizes_new_reasons` asserts `!matches!(reason,
//!     FailReason::Unknown)` rather than naming `FailReason::Disabled`
//!     etc. Naming a new variant would make the pre-fix tree fail to
//!     compile; the negative-`Unknown` form compiles against both
//!     trees and discriminates by VALUE (pre-fix maps these strings to
//!     the `#[serde(other)] Unknown` catch-all). It accepts any
//!     superset of the four variants in any order.

use tunnel::messages::client::{
    ClientDeviceAccessDenied, FailReason, ResourceDescriptionStaticDevicePool,
};

/// Root-cause reproduction: a Static Device Pool resource whose members
/// are described with the portal's `client_id` wire key must
/// deserialize, with every member decoded. Pre-fix the members carry
/// the wire key `client_id` while the struct expects `id`, so the whole
/// resource fails to decode (`missing field 'id'`) and is dropped.
#[test]
fn static_device_pool_decodes_members_with_portal_wire_key() {
    // Shape mirrors what the portal actually emits for a
    // `static_device_pool` resource (see the PR's own regression test).
    let json = r#"{
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "name": "IoT Devices",
        "devices": [
            {
                "client_id": "a3632404-4b03-4468-9fc0-4a4c82415ade",
                "ipv4": "100.64.1.38/32",
                "ipv6": "fd00:2021:1111::125/128"
            },
            {
                "client_id": "75fb9102-2651-49eb-9b0b-80f4eee182cb",
                "ipv4": "100.64.23.121/32",
                "ipv6": "fd00:2021:1111::1777/128"
            }
        ],
        "filters": []
    }"#;

    let desc: ResourceDescriptionStaticDevicePool = serde_json::from_str(json).expect(
        "a static device pool whose members use the portal's `client_id` wire key should \
         deserialize (pre-fix this fails with `missing field 'id'` and the resource is dropped)",
    );

    assert_eq!(desc.name, "IoT Devices");

    let member_ids: Vec<String> = desc.devices.iter().map(|m| m.id.to_string()).collect();
    assert_eq!(
        member_ids,
        vec![
            "a3632404-4b03-4468-9fc0-4a4c82415ade".to_string(),
            "75fb9102-2651-49eb-9b0b-80f4eee182cb".to_string(),
        ],
        "both device-pool members must decode from the portal's `client_id` wire key"
    );
}

/// The portal may legitimately omit `ipv4` and/or `ipv6` on a denial
/// depending on the reason. Such a message must still deserialize.
/// Pre-fix both addresses are required fields, so the decode fails.
#[test]
fn access_denied_tolerates_missing_addresses() {
    let json = r#"{ "reason": "missing_address" }"#;

    let parsed = serde_json::from_str::<ClientDeviceAccessDenied>(json);
    assert!(
        parsed.is_ok(),
        "a device-access denial that omits ipv4/ipv6 should deserialize, got: {parsed:?}"
    );
}

/// The portal now sends four additional failure reasons. Each must
/// deserialize to a dedicated variant rather than collapsing into the
/// `#[serde(other)] Unknown` catch-all (which is what happens pre-fix).
#[test]
fn fail_reason_recognizes_new_reasons() {
    for wire in [
        "disabled",
        "ambiguous_address",
        "missing_address",
        "invalid_address",
    ] {
        let reason: FailReason = serde_json::from_str(&format!("\"{wire}\""))
            .unwrap_or_else(|e| panic!("FailReason `{wire}` should deserialize: {e}"));
        assert!(
            !matches!(reason, FailReason::Unknown),
            "portal failure reason `{wire}` must map to a dedicated FailReason variant, \
             not the `Unknown` catch-all"
        );
    }
}
