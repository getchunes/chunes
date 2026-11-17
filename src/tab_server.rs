//! The loopback endpoint Chune ID reports audible tabs to.
//!
//! Same contract as `_handle_tab_report` in `presence.py`: one request per
//! connection, `POST /tabs` and `GET /state` on `127.0.0.1:52846`, and an
//! `X-Chunes-Protocol` header echoing the version a report arrived in.

use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::json;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::net::TcpListener;

use crate::protocol::{self, ProtocolError, Report, RequestHead};

const READ_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Default)]
pub struct TabState {
    pub report: Report,
    pub reported_at: f64,
    /// What the engine is currently publishing, echoed back to the extension.
    pub track: Option<String>,
    pub host: Option<String>,
}

impl TabState {
    pub fn fresh_report(&self, now: f64) -> Option<&Report> {
        protocol::report_is_fresh(self.reported_at, now).then_some(&self.report)
    }
}

pub type SharedState = Arc<Mutex<TabState>>;

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

fn reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        204 => "No Content",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        411 => "Length Required",
        413 => "Content Too Large",
        415 => "Unsupported Media Type",
        431 => "Request Header Fields Too Large",
        _ => "Internal Server Error",
    }
}

pub fn http_reply(status: u16, body: &[u8], protocol_version: u8) -> Vec<u8> {
    let mut head = format!(
        "HTTP/1.1 {status} {}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nContent-Length: {}\r\n",
        reason(status),
        body.len()
    );
    if (200..300).contains(&status) {
        head += &format!("X-Chunes-Protocol: {protocol_version}\r\n");
    }
    if !body.is_empty() {
        head += "Content-Type: application/json\r\n";
    }
    head += "\r\n";
    [head.as_bytes(), body].concat()
}

enum Failure {
    Protocol(ProtocolError),
    /// Short read, timeout, or an oversized head; Python answers 400.
    Framing,
}

impl From<ProtocolError> for Failure {
    fn from(error: ProtocolError) -> Self {
        Failure::Protocol(error)
    }
}

async fn read_request<S: AsyncRead + Unpin>(
    stream: &mut S,
) -> Result<(RequestHead, Vec<u8>, Vec<u8>), Failure> {
    let limit = protocol::MAX_HEADER_BYTES + 4;
    let mut buffer = Vec::with_capacity(1024);
    let head_end = tokio::time::timeout(READ_TIMEOUT, async {
        let mut chunk = [0u8; 1024];
        loop {
            // asyncio's readuntil(limit=...) refuses a separator that starts
            // past the limit, or a buffer that cannot hold one within it.
            if let Some(index) = buffer.windows(4).position(|w| w == b"\r\n\r\n") {
                return if index > limit {
                    Err(Failure::Framing)
                } else {
                    Ok(index)
                };
            }
            if buffer.len() + 1 > limit + 4 {
                return Err(Failure::Framing);
            }
            let read = stream
                .read(&mut chunk)
                .await
                .map_err(|_| Failure::Framing)?;
            if read == 0 {
                return Err(Failure::Framing);
            }
            buffer.extend_from_slice(&chunk[..read]);
        }
    })
    .await
    .map_err(|_| Failure::Framing)??;
    let head = buffer[..head_end].to_vec();
    let request = protocol::parse_request_head(&head)?;
    let mut body = buffer.split_off(head_end + 4);
    if let RequestHead::Report { content_length } = request {
        if body.len() < content_length {
            let mut rest = vec![0u8; content_length - body.len()];
            tokio::time::timeout(READ_TIMEOUT, stream.read_exact(&mut rest))
                .await
                .map_err(|_| Failure::Framing)?
                .map_err(|_| Failure::Framing)?;
            body.extend_from_slice(&rest);
        }
        body.truncate(content_length);
    }
    Ok((request, head, body))
}

/// Answer one request on `stream` and close it.
pub async fn handle<S: AsyncRead + AsyncWrite + Unpin>(mut stream: S, state: SharedState) {
    let reply = match read_request(&mut stream).await {
        Ok((RequestHead::State, _, _)) => {
            let state = state.lock().unwrap();
            let fresh = state.fresh_report(now()).is_some();
            let body = protocol::safe_public_state(Some(&state.report), fresh).to_string();
            http_reply(200, body.as_bytes(), protocol::PROTOCOL_VERSION)
        }
        Ok((RequestHead::Report { .. }, _, body)) => match protocol::parse_report_body(&body) {
            Ok((report, version)) => {
                let mut state = state.lock().unwrap();
                let hosts = |r: &Report| {
                    let mut h: Vec<String> = r.tabs.iter().map(|t| t.host.clone()).collect();
                    h.sort();
                    h.dedup();
                    h
                };
                if state.reported_at == 0.0
                    || hosts(&report) != hosts(&state.report)
                    || report.enabled != state.report.enabled
                {
                    crate::log!(
                        "Extension report (protocol {version}): enabled={}, audible hosts={:?}",
                        report.enabled,
                        hosts(&report)
                    );
                }
                state.report = report;
                state.reported_at = now();
                let body =
                    json!({"status": "ok", "track": state.track, "host": state.host}).to_string();
                http_reply(200, body.as_bytes(), version)
            }
            Err(error) => http_reply(error.status, b"", protocol::PROTOCOL_VERSION),
        },
        Err(Failure::Protocol(error)) => http_reply(error.status, b"", protocol::PROTOCOL_VERSION),
        Err(Failure::Framing) => http_reply(400, b"", protocol::PROTOCOL_VERSION),
    };
    let _ = stream.write_all(&reply).await;
    let _ = stream.shutdown().await;
}

/// Serve the endpoint until the process exits.
pub async fn serve(state: SharedState) -> std::io::Result<()> {
    let listener = TcpListener::bind(("127.0.0.1", protocol::TAB_REPORT_PORT)).await?;
    loop {
        let (stream, _) = listener.accept().await?;
        tokio::spawn(handle(stream, state.clone()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    async fn exchange(request: &[u8], state: SharedState) -> String {
        let (mut client, server) = duplex(64 * 1024);
        let task = tokio::spawn(handle(server, state));
        client.write_all(request).await.unwrap();
        let mut response = Vec::new();
        client.read_to_end(&mut response).await.unwrap();
        task.await.unwrap();
        String::from_utf8(response).unwrap()
    }

    fn post(body: &str) -> Vec<u8> {
        format!(
            "POST /tabs HTTP/1.1\r\nHost: 127.0.0.1:52846\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        )
        .into_bytes()
    }

    #[tokio::test]
    async fn accepts_a_report_and_echoes_its_protocol_version() {
        let state = SharedState::default();
        state.lock().unwrap().track = Some("Song - Artist".into());
        let body = r#"{"protocol":4,"enabled":true,"services":{"appleMusic":true,"soundcloud":true,"youtubeMusic":true},"tabs":[{"host":"soundcloud.com","mediaId":null,"title":"Song by Artist"}]}"#;
        let response = exchange(&post(body), state.clone()).await;
        assert!(response.starts_with("HTTP/1.1 200 OK\r\n"), "{response}");
        assert!(response.contains("\r\nX-Chunes-Protocol: 4\r\n"));
        assert!(
            response.ends_with(r#"{"host":null,"status":"ok","track":"Song - Artist"}"#),
            "{response}"
        );
        let state = state.lock().unwrap();
        assert_eq!(state.report.tabs[0].host, "soundcloud.com");
        assert!(state.fresh_report(now()).is_some());
    }

    #[tokio::test]
    async fn head_size_limits_match_asyncio_readuntil() {
        // Up to four bytes over the head limit still reach the parser, which
        // answers 431 as Python did; anything longer is a framing error.
        let padded = |len: usize| {
            let mut head = b"GET /state HTTP/1.1\r\nX-Pad: ".to_vec();
            head.resize(len, b'a');
            head.extend_from_slice(b"\r\n\r\n");
            head
        };
        let over = protocol::MAX_HEADER_BYTES + 4;
        let response = exchange(&padded(over), SharedState::default()).await;
        assert!(response.starts_with("HTTP/1.1 431 "), "{response}");
        let response = exchange(&padded(over + 1), SharedState::default()).await;
        assert!(response.starts_with("HTTP/1.1 400 "), "{response}");
    }

    #[tokio::test]
    async fn state_reports_unfresh_before_any_report() {
        let response = exchange(
            b"GET /state HTTP/1.1\r\nHost: localhost\r\n\r\n",
            SharedState::default(),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
        assert!(response.contains(r#""fresh":false"#));
    }

    #[tokio::test]
    async fn errors_carry_no_protocol_header() {
        let response = exchange(&post("{}"), SharedState::default()).await;
        assert!(response.starts_with("HTTP/1.1 400 Bad Request\r\n"));
        assert!(!response.contains("X-Chunes-Protocol"));
        let response = exchange(
            b"GET /tabs HTTP/1.1\r\nHost: localhost\r\n\r\n",
            SharedState::default(),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 405 "));
    }
}
