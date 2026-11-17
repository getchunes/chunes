//! Discord Rich Presence over the desktop client's local IPC pipe.
//!
//! Replaces `pypresence` plus the `_read_output` patch in `presence.py`.
//! Frames are a little-endian `(opcode, length)` header followed by JSON.
//! The client is generic over the stream so the framing is tested without
//! Discord; on Windows the stream is the `\\.\pipe\discord-ipc-N` named pipe.

use std::fmt;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

pub const OP_HANDSHAKE: u32 = 0;
pub const OP_FRAME: u32 = 1;
pub const OP_CLOSE: u32 = 2;
pub const OP_PING: u32 = 3;
pub const OP_PONG: u32 = 4;
pub const MAX_FRAME_BYTES: u32 = 1024 * 1024;
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(15);
/// `ActivityType.LISTENING`.
const LISTENING: u8 = 2;

#[derive(Debug)]
pub enum IpcError {
    /// The pipe closed, timed out, or sent something that is not Discord IPC.
    Closed(String),
    /// Discord answered the command with an `ERROR` event.
    Server(String),
}

impl fmt::Display for IpcError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IpcError::Closed(why) => write!(f, "Discord pipe closed: {why}"),
            IpcError::Server(message) => write!(f, "Discord error: {message}"),
        }
    }
}

impl std::error::Error for IpcError {}

impl From<std::io::Error> for IpcError {
    fn from(error: std::io::Error) -> Self {
        IpcError::Closed(error.to_string())
    }
}

pub async fn write_frame<W: AsyncWrite + Unpin>(
    writer: &mut W,
    op: u32,
    payload: &Value,
) -> Result<(), IpcError> {
    let body = serde_json::to_vec(payload).map_err(|e| IpcError::Closed(e.to_string()))?;
    let mut frame = Vec::with_capacity(8 + body.len());
    frame.extend_from_slice(&op.to_le_bytes());
    frame.extend_from_slice(&(body.len() as u32).to_le_bytes());
    frame.extend_from_slice(&body);
    writer.write_all(&frame).await?;
    writer.flush().await?;
    Ok(())
}

pub async fn read_frame<R: AsyncRead + Unpin>(reader: &mut R) -> Result<(u32, Value), IpcError> {
    let mut header = [0u8; 8];
    reader.read_exact(&mut header).await?;
    let op = u32::from_le_bytes(header[..4].try_into().unwrap());
    let length = u32::from_le_bytes(header[4..].try_into().unwrap());
    if length > MAX_FRAME_BYTES {
        return Err(IpcError::Closed("oversized frame".into()));
    }
    let mut body = vec![0u8; length as usize];
    reader.read_exact(&mut body).await?;
    let payload = serde_json::from_slice(&body).map_err(|e| IpcError::Closed(e.to_string()))?;
    Ok((op, payload))
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Activity {
    pub details: String,
    pub state: Option<String>,
    /// Epoch seconds, as Chunes sends them today.
    pub start: Option<i64>,
    pub end: Option<i64>,
    pub large_image: Option<String>,
    pub large_text: Option<String>,
    pub buttons: Vec<(String, String)>,
}

impl Activity {
    pub fn to_json(&self) -> Value {
        let mut activity = json!({"type": LISTENING, "details": self.details});
        if let Some(state) = &self.state {
            activity["state"] = json!(state);
        }
        if self.start.is_some() || self.end.is_some() {
            let mut timestamps = json!({});
            if let Some(start) = self.start {
                timestamps["start"] = json!(start);
            }
            if let Some(end) = self.end {
                timestamps["end"] = json!(end);
            }
            activity["timestamps"] = timestamps;
        }
        if self.large_image.is_some() || self.large_text.is_some() {
            let mut assets = json!({});
            if let Some(image) = &self.large_image {
                assets["large_image"] = json!(image);
            }
            if let Some(text) = &self.large_text {
                assets["large_text"] = json!(text);
            }
            activity["assets"] = assets;
        }
        if !self.buttons.is_empty() {
            activity["buttons"] = self
                .buttons
                .iter()
                .map(|(label, url)| json!({"label": label, "url": url}))
                .collect();
        }
        activity
    }
}

pub struct Client<S> {
    stream: S,
    next_nonce: u64,
}

impl<S: AsyncRead + AsyncWrite + Unpin> Client<S> {
    /// Send the handshake and wait for Discord's `READY` dispatch.
    pub async fn handshake(mut stream: S, client_id: &str) -> Result<Self, IpcError> {
        write_frame(
            &mut stream,
            OP_HANDSHAKE,
            &json!({"v": 1, "client_id": client_id}),
        )
        .await?;
        let mut client = Self {
            stream,
            next_nonce: 1,
        };
        client
            .await_reply(|payload| payload["cmd"] == "DISPATCH" && payload["evt"] == "READY")
            .await?;
        Ok(client)
    }

    /// Read until `matches` accepts a frame, servicing pings on the way.
    async fn await_reply(&mut self, matches: impl Fn(&Value) -> bool) -> Result<Value, IpcError> {
        let deadline = tokio::time::Instant::now() + RESPONSE_TIMEOUT;
        loop {
            let (op, payload) = tokio::time::timeout_at(deadline, read_frame(&mut self.stream))
                .await
                .map_err(|_| IpcError::Closed("response timed out".into()))??;
            match op {
                OP_CLOSE => {
                    return Err(IpcError::Closed(
                        payload["message"].as_str().unwrap_or("closed").into(),
                    ))
                }
                OP_PING => {
                    write_frame(&mut self.stream, OP_PONG, &payload).await?;
                    continue;
                }
                OP_PONG => continue,
                OP_FRAME if payload.is_object() => {}
                _ => return Err(IpcError::Closed(format!("unexpected opcode {op}"))),
            }
            if payload["evt"] == "ERROR" {
                let message = payload["data"]["message"]
                    .as_str()
                    .filter(|m| !m.is_empty());
                return Err(IpcError::Server(
                    message.unwrap_or("Discord RPC error").into(),
                ));
            }
            if matches(&payload) {
                return Ok(payload);
            }
            // Nonce-less DISPATCH events are unrelated to an outstanding
            // command and must not satisfy it.
        }
    }

    pub async fn command(&mut self, cmd: &str, args: Value) -> Result<Value, IpcError> {
        let nonce = self.next_nonce.to_string();
        self.next_nonce += 1;
        write_frame(
            &mut self.stream,
            OP_FRAME,
            &json!({"cmd": cmd, "args": args, "nonce": nonce}),
        )
        .await?;
        self.await_reply(|payload| payload["cmd"] == cmd && payload["nonce"] == nonce.as_str())
            .await
    }

    /// Set the Listening activity, or clear it with `None`.
    pub async fn set_activity(
        &mut self,
        pid: u32,
        activity: Option<&Activity>,
    ) -> Result<(), IpcError> {
        let activity = activity.map_or(Value::Null, Activity::to_json);
        self.command("SET_ACTIVITY", json!({"pid": pid, "activity": activity}))
            .await?;
        Ok(())
    }
}

/// Connect to the first Discord IPC pipe that answers.
#[cfg(windows)]
pub async fn connect(
    client_id: &str,
) -> Result<Client<tokio::net::windows::named_pipe::NamedPipeClient>, IpcError> {
    use tokio::net::windows::named_pipe::ClientOptions;
    // Keep the first real failure: the empty pipe slots after it all say
    // "not found", which would hide why Discord's own pipe refused us.
    let mut failure: Option<IpcError> = None;
    for index in 0..10 {
        match ClientOptions::new().open(format!(r"\\.\pipe\discord-ipc-{index}")) {
            Ok(pipe) => match Client::handshake(pipe, client_id).await {
                Ok(client) => return Ok(client),
                Err(error) => {
                    failure.get_or_insert(error);
                }
            },
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                failure.get_or_insert(error.into());
            }
        }
    }
    Err(failure.unwrap_or_else(|| IpcError::Closed("Discord is not running".into())))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    async fn fake_discord<S: AsyncRead + AsyncWrite + Unpin>(mut pipe: S) {
        let (op, hello) = read_frame(&mut pipe).await.unwrap();
        assert_eq!(op, OP_HANDSHAKE);
        assert_eq!(hello, json!({"v": 1, "client_id": "123"}));
        write_frame(
            &mut pipe,
            OP_FRAME,
            &json!({"cmd": "DISPATCH", "evt": "READY", "data": {}}),
        )
        .await
        .unwrap();

        let (op, command) = read_frame(&mut pipe).await.unwrap();
        assert_eq!(op, OP_FRAME);
        assert_eq!(command["cmd"], "SET_ACTIVITY");
        assert_eq!(command["args"]["activity"]["type"], 2);
        assert_eq!(command["args"]["activity"]["details"], "Song");
        // Discord can interleave a ping and an unrelated event before the reply.
        write_frame(&mut pipe, OP_PING, &json!({"n": 1}))
            .await
            .unwrap();
        let (op, pong) = read_frame(&mut pipe).await.unwrap();
        assert_eq!((op, pong), (OP_PONG, json!({"n": 1})));
        write_frame(
            &mut pipe,
            OP_FRAME,
            &json!({"cmd": "DISPATCH", "evt": "ACTIVITY_JOIN"}),
        )
        .await
        .unwrap();
        write_frame(
            &mut pipe,
            OP_FRAME,
            &json!({"cmd": "SET_ACTIVITY", "nonce": command["nonce"], "evt": null}),
        )
        .await
        .unwrap();

        let (_, clear) = read_frame(&mut pipe).await.unwrap();
        assert_eq!(clear["args"]["activity"], Value::Null);
        write_frame(&mut pipe, OP_FRAME, &json!({"cmd": "SET_ACTIVITY", "nonce": clear["nonce"], "evt": "ERROR", "data": {"message": "nope"}}))
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn sets_and_clears_activity_through_pings_and_unrelated_events() {
        let (app, discord) = duplex(4096);
        let server = tokio::spawn(fake_discord(discord));
        let mut client = Client::handshake(app, "123").await.unwrap();
        let activity = Activity {
            details: "Song".into(),
            state: Some("Artist".into()),
            ..Default::default()
        };
        client.set_activity(42, Some(&activity)).await.unwrap();
        match client.set_activity(42, None).await {
            Err(IpcError::Server(message)) => assert_eq!(message, "nope"),
            other => panic!("{other:?}"),
        }
        server.await.unwrap();
    }

    #[tokio::test]
    async fn rejects_oversized_frames() {
        let (mut app, mut discord) = duplex(64);
        discord.write_all(&OP_FRAME.to_le_bytes()).await.unwrap();
        discord
            .write_all(&(MAX_FRAME_BYTES + 1).to_le_bytes())
            .await
            .unwrap();
        assert!(matches!(
            read_frame(&mut app).await,
            Err(IpcError::Closed(_))
        ));
    }

    #[test]
    fn activity_matches_the_shape_presence_py_sends() {
        let activity = Activity {
            details: "Song".into(),
            state: Some("Artist".into()),
            start: Some(100),
            end: Some(300),
            large_image: Some("https://i1.sndcdn.com/a.jpg".into()),
            large_text: Some("SoundCloud".into()),
            buttons: vec![(
                "Play on SoundCloud".into(),
                "https://soundcloud.com/a/b".into(),
            )],
        };
        assert_eq!(
            activity.to_json(),
            json!({
                "type": 2,
                "details": "Song",
                "state": "Artist",
                "timestamps": {"start": 100, "end": 300},
                "assets": {"large_image": "https://i1.sndcdn.com/a.jpg", "large_text": "SoundCloud"},
                "buttons": [{"label": "Play on SoundCloud", "url": "https://soundcloud.com/a/b"}],
            })
        );
    }
}
