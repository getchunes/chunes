# Third-Party Notices

Chunes is licensed under Apache-2.0. `Chunes.exe` is a native Rust executable
that statically compiles in the open-source crates below, each under its own
license. Those licenses continue to apply to the respective components. The
list covers every crate linked into the Windows binary, as pinned by
`Cargo.lock`; build-time-only crates and procedural macros are not shipped.

| Crate | Version | License | Source |
| --- | --- | --- | --- |
| base64 | 0.23.1 | MIT OR Apache-2.0 | https://github.com/marshallpierce/rust-base64 |
| base64ct | 1.8.3 | Apache-2.0 OR MIT | https://github.com/RustCrypto/formats |
| bytes | 1.12.1 | MIT | https://github.com/tokio-rs/bytes |
| der | 0.8.3 | Apache-2.0 OR MIT | https://github.com/RustCrypto/formats |
| form_urlencoded | 1.2.2 | MIT OR Apache-2.0 | https://github.com/servo/rust-url |
| http | 1.5.0 | MIT OR Apache-2.0 | https://github.com/hyperium/http |
| httparse | 1.10.1 | MIT OR Apache-2.0 | https://github.com/seanmonstar/httparse |
| ICU4X: icu_collections, icu_locale_core, icu_normalizer, icu_normalizer_data, icu_properties, icu_properties_data, icu_provider; litemap; potential_utf; tinystr; writeable; yoke; zerofrom; zerotrie; zerovec | icu_* 2.4.0; litemap 0.8.3; potential_utf 0.1.6; tinystr 0.8.5; writeable 0.6.4; yoke 0.8.4; zerofrom 0.1.8; zerotrie 0.2.5; zerovec 0.11.9 | Unicode-3.0 | https://github.com/unicode-org/icu4x |
| idna | 1.1.0 | MIT OR Apache-2.0 | https://github.com/servo/rust-url |
| idna_adapter | 1.2.2 | Apache-2.0 OR MIT | https://github.com/hsivonen/idna_adapter |
| itoa | 1.0.19 | MIT OR Apache-2.0 | https://github.com/dtolnay/itoa |
| log | 0.4.35 | MIT OR Apache-2.0 | https://github.com/rust-lang/log |
| memchr | 2.8.3 | Unlicense OR MIT | https://github.com/BurntSushi/memchr |
| mio | 1.2.5 | MIT | https://github.com/tokio-rs/mio |
| native-tls | 0.2.18 | MIT OR Apache-2.0 | https://github.com/rust-native-tls/rust-native-tls |
| pem-rfc7468 | 1.0.0 | Apache-2.0 OR MIT | https://github.com/RustCrypto/formats |
| percent-encoding | 2.3.2 | MIT OR Apache-2.0 | https://github.com/servo/rust-url |
| pin-project-lite | 0.2.17 | Apache-2.0 OR MIT | https://github.com/taiki-e/pin-project-lite |
| rustls-pki-types | 1.16.0 | MIT OR Apache-2.0 | https://github.com/rustls/pki-types |
| schannel | 0.1.30 | MIT | https://github.com/steffengy/schannel-rs |
| serde, serde_core | 1.0.229 | MIT OR Apache-2.0 | https://github.com/serde-rs/serde |
| serde_json | 1.0.154 | MIT OR Apache-2.0 | https://github.com/serde-rs/json |
| smallvec | 1.16.5 | MIT OR Apache-2.0 | https://github.com/servo/rust-smallvec |
| socket2 | 0.6.5 | MIT OR Apache-2.0 | https://github.com/rust-lang/socket2 |
| stable_deref_trait | 1.2.1 | MIT OR Apache-2.0 | https://github.com/storyyeller/stable_deref_trait |
| tokio | 1.54.1 | MIT | https://github.com/tokio-rs/tokio |
| ureq, ureq-proto | 3.5.2; 0.7.1 | MIT OR Apache-2.0 | https://github.com/algesten/ureq |
| url | 2.5.8 | MIT OR Apache-2.0 | https://github.com/servo/rust-url |
| utf8-zero | 0.8.1 | MIT OR Apache-2.0 | https://github.com/algesten/utf8-zero |
| utf8_iter, utf_types | 1.1.0; 1.0.0 | Apache-2.0 OR MIT | https://github.com/hsivonen |
| windows-rs: windows, windows-collections, windows-core, windows-future, windows-link, windows-numerics, windows-result, windows-strings, windows-sys, windows-threading | windows 0.61.3; windows-collections 0.2.0; windows-core 0.61.2; windows-future 0.2.1; windows-link 0.1.3 and 0.2.1; windows-numerics 0.2.0; windows-result 0.3.4; windows-strings 0.4.2; windows-sys 0.61.2; windows-threading 0.1.0 | MIT OR Apache-2.0 | https://github.com/microsoft/windows-rs |
| zeroize | 1.9.1 | Apache-2.0 OR MIT | https://github.com/RustCrypto/utils |
| zmij | 1.0.23 | MIT | https://github.com/dtolnay/zmij |

The complete corresponding Chunes application source and reproducible build
instructions are available at https://github.com/getchunes/chunes. Upstream
source and license texts are available at the linked projects.

## Chunes musical note

The Chunes musical note geometry is adapted from the Bootstrap Icons
`music-note-beamed` icon:

https://icons.getbootstrap.com/icons/music-note-beamed/

The MIT License (MIT)

Copyright (c) 2019-2024 The Bootstrap Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## Service names

Discord, SoundCloud, YouTube, YouTube Music, Windows, and GitHub are trademarks
or service marks of their respective owners. Chunes is not affiliated with,
sponsored by, or endorsed by those services.
