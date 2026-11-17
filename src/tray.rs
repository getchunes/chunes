//! The notification-area icon and its menu, on plain Win32.
//!
//! Replaces pystray. A hidden top-level window owns the icon, so it also gets
//! the session's close and shutdown messages and Explorer's TaskbarCreated
//! broadcast (the icon is re-added when Explorer restarts). The menu is
//! rebuilt from `menu::build` every time it opens.

use std::cell::RefCell;
use std::rc::Rc;

use windows::core::{w, HSTRING, PCWSTR};
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_INFO, NIF_MESSAGE, NIF_TIP, NIIF_INFO, NIM_ADD, NIM_DELETE,
    NIM_MODIFY, NOTIFYICONDATAW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DestroyWindow,
    DispatchMessageW, GetCursorPos, GetMessageW, GetSystemMetrics, LoadIconW, LoadImageW,
    PostMessageW, PostQuitMessage, RegisterClassW, RegisterWindowMessageW, SetForegroundWindow,
    TrackPopupMenu, TranslateMessage, HICON, IDI_APPLICATION, IMAGE_ICON, LR_DEFAULTCOLOR,
    MENU_ITEM_FLAGS, MF_CHECKED, MF_GRAYED, MF_SEPARATOR, MF_STRING, MSG, SM_CXSMICON, SM_CYSMICON,
    TPM_NONOTIFY, TPM_RETURNCMD, TPM_RIGHTBUTTON, WINDOW_EX_STYLE, WM_APP, WM_CLOSE, WM_DESTROY,
    WM_ENDSESSION, WM_NULL, WM_QUERYENDSESSION, WM_RBUTTONUP, WNDCLASSW, WS_OVERLAPPED,
};

use crate::menu::{self, Command, Entry};

const CALLBACK_MESSAGE: u32 = WM_APP + 1;
const ICON_ID: u32 = 1;
/// The ICON resource the build embeds in Chunes.exe.
const ICON_RESOURCE: u16 = 1;

/// What the tray asks of the rest of the app.
pub trait Host {
    fn menu(&self) -> Vec<Entry>;
    /// Run a menu command. `Quit` is handled by the tray itself.
    fn command(&self, command: Command);
    /// The icon is up; `tray` can show a notification now.
    fn ready(&self, tray: &Tray);
}

/// The icon, once it is in the notification area.
pub struct Tray {
    hwnd: HWND,
    icon: HICON,
}

thread_local! {
    static STATE: RefCell<Option<(Rc<dyn Host>, Tray)>> = const { RefCell::new(None) };
    static TASKBAR_CREATED: std::cell::Cell<u32> = const { std::cell::Cell::new(0) };
}

fn copy_wide<const N: usize>(target: &mut [u16; N], text: &str) {
    for (slot, unit) in target
        .iter_mut()
        .zip(text.encode_utf16().take(N - 1).chain(std::iter::once(0)))
    {
        *slot = unit;
    }
}

impl Tray {
    fn data(&self) -> NOTIFYICONDATAW {
        NOTIFYICONDATAW {
            cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: self.hwnd,
            uID: ICON_ID,
            ..Default::default()
        }
    }

    fn add(&self) -> bool {
        let mut data = self.data();
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
        data.uCallbackMessage = CALLBACK_MESSAGE;
        data.hIcon = self.icon;
        copy_wide(&mut data.szTip, "Chunes");
        // SAFETY: `data` is a fully initialized NOTIFYICONDATAW.
        unsafe { Shell_NotifyIconW(NIM_ADD, &data) }.as_bool()
    }

    fn remove(&self) {
        let data = self.data();
        // SAFETY: as above.
        let _ = unsafe { Shell_NotifyIconW(NIM_DELETE, &data) };
    }

    /// Show a balloon notification from the icon. False when Windows
    /// refused it (no notification area, notifications off for the shell).
    pub fn notify(&self, title: &str, text: &str) -> bool {
        let mut data = self.data();
        data.uFlags = NIF_INFO;
        data.dwInfoFlags = NIIF_INFO;
        copy_wide(&mut data.szInfo, text);
        copy_wide(&mut data.szInfoTitle, title);
        // SAFETY: as above.
        unsafe { Shell_NotifyIconW(NIM_MODIFY, &data) }.as_bool()
    }
}

fn load_icon() -> HICON {
    // SAFETY: plain resource lookups in this module.
    unsafe {
        let module = GetModuleHandleW(None).ok();
        let image = LoadImageW(
            module.map(Into::into),
            PCWSTR(ICON_RESOURCE as usize as *const u16),
            IMAGE_ICON,
            GetSystemMetrics(SM_CXSMICON),
            GetSystemMetrics(SM_CYSMICON),
            LR_DEFAULTCOLOR,
        );
        match image {
            Ok(handle) => HICON(handle.0),
            Err(_) => LoadIconW(None, IDI_APPLICATION).unwrap_or_default(),
        }
    }
}

fn show_menu(hwnd: HWND, host: &dyn Host) -> Option<Command> {
    let entries = host.menu();
    let mut commands = Vec::new();
    // SAFETY: the menu is created, shown modally and destroyed here.
    unsafe {
        let popup = CreatePopupMenu().ok()?;
        for entry in &entries {
            let (flags, label, command): (MENU_ITEM_FLAGS, String, Option<Command>) = match entry {
                Entry::Separator => (MF_SEPARATOR, String::new(), None),
                Entry::Text(text) => (MF_STRING | MF_GRAYED, menu::escape_menu_text(text), None),
                Entry::Check {
                    label,
                    checked,
                    command,
                } => (
                    if *checked {
                        MF_STRING | MF_CHECKED
                    } else {
                        MF_STRING
                    },
                    (*label).to_owned(),
                    Some(*command),
                ),
                Entry::Action { label, command } => {
                    (MF_STRING, (*label).to_owned(), Some(*command))
                }
            };
            let id = match command {
                Some(command) => {
                    commands.push(command);
                    commands.len()
                }
                None => 0,
            };
            let text = HSTRING::from(label);
            let _ = AppendMenuW(
                popup,
                flags,
                id,
                if flags == MF_SEPARATOR {
                    PCWSTR::null()
                } else {
                    PCWSTR(text.as_ptr())
                },
            );
        }
        let mut cursor = POINT::default();
        let _ = GetCursorPos(&mut cursor);
        // Without this the menu does not close when the user clicks away.
        let _ = SetForegroundWindow(hwnd);
        let chosen = TrackPopupMenu(
            popup,
            TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
            cursor.x,
            cursor.y,
            None,
            hwnd,
            None,
        );
        let _ = PostMessageW(Some(hwnd), WM_NULL, WPARAM(0), LPARAM(0));
        let _ = DestroyMenu(popup);
        let chosen = chosen.0 as usize;
        (chosen > 0)
            .then(|| commands.get(chosen - 1).copied())
            .flatten()
    }
}

fn host() -> Option<Rc<dyn Host>> {
    STATE.with(|state| state.borrow().as_ref().map(|(host, _)| host.clone()))
}

extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        CALLBACK_MESSAGE if lparam.0 as u32 == WM_RBUTTONUP => {
            if let Some(host) = host() {
                match show_menu(hwnd, host.as_ref()) {
                    Some(Command::Quit) => {
                        // SAFETY: our own window.
                        let _ = unsafe { DestroyWindow(hwnd) };
                    }
                    Some(command) => host.command(command),
                    None => {}
                }
            }
            LRESULT(0)
        }
        WM_CLOSE => {
            // SAFETY: our own window.
            let _ = unsafe { DestroyWindow(hwnd) };
            LRESULT(0)
        }
        // Never hold up a logoff or shutdown.
        WM_QUERYENDSESSION => LRESULT(1),
        WM_ENDSESSION => {
            if wparam.0 != 0 {
                // SAFETY: our own window.
                let _ = unsafe { DestroyWindow(hwnd) };
            }
            LRESULT(0)
        }
        WM_DESTROY => {
            STATE.with(|state| {
                if let Some((_, tray)) = state.borrow().as_ref() {
                    tray.remove();
                }
            });
            // SAFETY: ends this thread's message loop.
            unsafe { PostQuitMessage(0) };
            LRESULT(0)
        }
        _ if message != 0 && message == TASKBAR_CREATED.with(|m| m.get()) => {
            // Explorer restarted and forgot every icon.
            STATE.with(|state| {
                if let Some((_, tray)) = state.borrow().as_ref() {
                    tray.add();
                }
            });
            LRESULT(0)
        }
        // SAFETY: default handling for everything else.
        _ => unsafe { DefWindowProcW(hwnd, message, wparam, lparam) },
    }
}

/// Show the icon and run the message loop until Quit, a close request, or
/// the end of the session.
pub fn run(host: Rc<dyn Host>) -> windows::core::Result<()> {
    // SAFETY: standard window-class registration and creation on this
    // thread; the window lives until the loop below ends.
    unsafe {
        let instance = GetModuleHandleW(None)?;
        let class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance.into(),
            lpszClassName: w!("ChunesTray"),
            ..Default::default()
        };
        RegisterClassW(&class);
        TASKBAR_CREATED.with(|m| m.set(RegisterWindowMessageW(w!("TaskbarCreated"))));
        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("ChunesTray"),
            w!("Chunes"),
            WS_OVERLAPPED,
            0,
            0,
            0,
            0,
            None,
            None,
            Some(instance.into()),
            None,
        )?;
        let tray = Tray {
            hwnd,
            icon: load_icon(),
        };
        if !tray.add() {
            crate::log!("Windows did not accept the tray icon yet; waiting for the taskbar.");
        }
        host.ready(&tray);
        STATE.with(|state| *state.borrow_mut() = Some((host, tray)));

        let mut message = MSG::default();
        while GetMessageW(&mut message, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        STATE.with(|state| state.borrow_mut().take());
    }
    Ok(())
}
