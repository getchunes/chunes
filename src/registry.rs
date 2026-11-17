//! The few `HKEY_CURRENT_USER` reads and writes Chunes makes, replacing
//! Python's `winreg`.

/// A registry value as Chunes cares about it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Value {
    Dword(u32),
    /// `REG_SZ` or `REG_EXPAND_SZ`, unexpanded.
    String(String),
    /// Any other type.
    Other,
}

#[cfg(windows)]
mod imp {
    use super::Value;
    use windows::core::HSTRING;
    use windows::Win32::System::Registry::{
        RegDeleteKeyValueW, RegGetValueW, RegSetKeyValueW, HKEY_CURRENT_USER, REG_DWORD,
        REG_EXPAND_SZ, REG_SZ, REG_VALUE_TYPE, RRF_NOEXPAND, RRF_RT_ANY,
    };

    /// `None` when the key or value is missing or unreadable.
    pub fn get(subkey: &str, name: &str) -> Option<Value> {
        let (subkey, name) = (HSTRING::from(subkey), HSTRING::from(name));
        let mut kind = REG_VALUE_TYPE::default();
        let mut size = 0u32;
        // SAFETY: a size query with no buffer.
        let sized = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                &subkey,
                &name,
                RRF_RT_ANY | RRF_NOEXPAND,
                Some(&mut kind),
                None,
                Some(&mut size),
            )
        };
        if sized.is_err() {
            return None;
        }
        let mut buffer = vec![0u8; size as usize + 2];
        let mut size = buffer.len() as u32;
        // SAFETY: `buffer` holds `size` bytes.
        let read = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                &subkey,
                &name,
                RRF_RT_ANY | RRF_NOEXPAND,
                Some(&mut kind),
                Some(buffer.as_mut_ptr().cast()),
                Some(&mut size),
            )
        };
        if read.is_err() {
            return None;
        }
        buffer.truncate(size as usize);
        Some(match kind {
            REG_DWORD if buffer.len() == 4 => {
                Value::Dword(u32::from_le_bytes(buffer[..4].try_into().unwrap()))
            }
            REG_SZ | REG_EXPAND_SZ => {
                let wide: Vec<u16> = buffer
                    .chunks_exact(2)
                    .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
                    .take_while(|&unit| unit != 0)
                    .collect();
                Value::String(String::from_utf16_lossy(&wide))
            }
            _ => Value::Other,
        })
    }

    pub fn set_dword(subkey: &str, name: &str, value: u32) -> windows::core::Result<()> {
        let bytes = value.to_le_bytes();
        // SAFETY: `bytes` is a 4-byte DWORD.
        unsafe {
            RegSetKeyValueW(
                HKEY_CURRENT_USER,
                &HSTRING::from(subkey),
                &HSTRING::from(name),
                REG_DWORD.0,
                Some(bytes.as_ptr().cast()),
                bytes.len() as u32,
            )
        }
        .ok()
    }

    pub fn set_string(subkey: &str, name: &str, value: &str) -> windows::core::Result<()> {
        let wide: Vec<u16> = value.encode_utf16().chain(std::iter::once(0)).collect();
        // SAFETY: `wide` is a NUL-terminated UTF-16 string of the given size.
        unsafe {
            RegSetKeyValueW(
                HKEY_CURRENT_USER,
                &HSTRING::from(subkey),
                &HSTRING::from(name),
                REG_SZ.0,
                Some(wide.as_ptr().cast()),
                (wide.len() * 2) as u32,
            )
        }
        .ok()
    }

    pub fn delete(subkey: &str, name: &str) -> windows::core::Result<()> {
        // SAFETY: plain string arguments.
        unsafe {
            RegDeleteKeyValueW(
                HKEY_CURRENT_USER,
                &HSTRING::from(subkey),
                &HSTRING::from(name),
            )
        }
        .ok()
    }
}

#[cfg(windows)]
pub use imp::{delete, get, set_dword, set_string};
