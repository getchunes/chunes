//! Console runner for the presence engine, until the tray app lands.

#[cfg(not(windows))]
fn main() {
    eprintln!("Chunes reads the Windows media session and only runs on Windows.");
    std::process::exit(1);
}

#[cfg(windows)]
#[tokio::main]
async fn main() {
    use chunes::presence::Switches;
    use chunes::tab_server::SharedState;

    chunes::engine::run(SharedState::default(), Switches::default).await;
}
