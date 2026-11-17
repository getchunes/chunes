//! HTTPS GET for the artwork lookup and the release check.
//!
//! Windows' own TLS stack (Schannel) verifies certificates against the
//! Windows certificate store, as Python's `urllib` did.

use std::time::Duration;

pub struct Request<'a> {
    pub url: &'a str,
    pub headers: &'a [(&'a str, &'a str)],
    pub timeout: Duration,
    /// Refuse a body longer than this.
    pub max_bytes: u64,
    /// Fail on any redirect instead of following it.
    pub no_redirects: bool,
}

#[cfg(windows)]
pub fn get(request: &Request) -> Result<String, String> {
    use ureq::tls::{RootCerts, TlsConfig, TlsProvider};

    let agent: ureq::Agent = ureq::Agent::config_builder()
        .timeout_global(Some(request.timeout))
        .max_redirects(if request.no_redirects { 0 } else { 10 })
        .tls_config(
            TlsConfig::builder()
                .provider(TlsProvider::NativeTls)
                .root_certs(RootCerts::PlatformVerifier)
                .build(),
        )
        .build()
        .into();
    let mut call = agent.get(request.url);
    for (name, value) in request.headers {
        call = call.header(*name, *value);
    }
    let mut response = call.call().map_err(|e| e.to_string())?;
    if response.status() != 200 {
        return Err(format!("HTTP {}", response.status()));
    }
    let body = response
        .body_mut()
        .with_config()
        .limit(request.max_bytes)
        .read_to_vec()
        .map_err(|e| e.to_string())?;
    Ok(String::from_utf8_lossy(&body).into_owned())
}

#[cfg(not(windows))]
pub fn get(_request: &Request) -> Result<String, String> {
    Err("HTTPS requests are only made on Windows".into())
}
