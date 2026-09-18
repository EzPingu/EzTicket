use discord_rich_presence::{activity, DiscordIpc, DiscordIpcClient};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::Manager;

const DISCORD_APPLICATION_ID: &str = "1530649733822156931";

struct RichPresenceHandle {
    stop: Sender<()>,
}

#[derive(Default)]
struct OAuthCallbackState {
    query: Mutex<Option<String>>,
    cancel: Mutex<Option<Arc<AtomicBool>>>,
}

fn start_rich_presence() -> RichPresenceHandle {
    let (stop, receiver) = mpsc::channel();
    std::thread::spawn(move || rich_presence_worker(receiver));
    RichPresenceHandle { stop }
}

fn rich_presence_worker(stop: Receiver<()>) {
    let Ok(mut client) = DiscordIpcClient::new(DISCORD_APPLICATION_ID) else {
        return;
    };
    if client.connect().is_err() {
        return;
    }

    let assets = activity::Assets::new()
        .large_image("logo")
        .large_text("EzTicket Manager");
    let presence = activity::Activity::new()
        .details("Gestione server e ticket")
        .state("Online")
        .assets(assets)
        .timestamps(
            activity::Timestamps::new().start(
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|duration| duration.as_secs() as i64)
                    .unwrap_or_default(),
            ),
        );

    if client.set_activity(presence).is_err() {
        let _ = client.close();
        return;
    }

    let _ = stop.recv();
    let _ = client.clear_activity();
    let _ = client.close();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            app.manage(start_rich_presence());
            Ok(())
        })
        .manage(Arc::new(OAuthCallbackState::default()))
        .invoke_handler(tauri::generate_handler![
            start_oauth_callback,
            wait_oauth_callback,
            cancel_oauth_callback
        ])
        .plugin(tauri_plugin_opener::init())
        .build(tauri::generate_context!())
        .expect("error while building tauri application");
    app.run(|app: &tauri::AppHandle<tauri::Wry>, event: tauri::RunEvent| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                let handle = app.state::<RichPresenceHandle>();
                let _ = handle.stop.send(());
            }
        });
}

#[tauri::command]
fn start_oauth_callback(
    callback: tauri::State<'_, Arc<OAuthCallbackState>>,
) -> Result<(), String> {
    let listener = std::net::TcpListener::bind("127.0.0.1:8765")
        .map_err(|error| format!("Impossibile avviare il callback OAuth locale: {error}"))?;
    listener
        .set_nonblocking(true)
        .map_err(|error| format!("Impossibile configurare il callback OAuth locale: {error}"))?;
    let cancel = Arc::new(AtomicBool::new(false));
    if let Ok(mut stored) = callback.query.lock() {
        *stored = None;
    } else {
        return Err("Impossibile preparare il callback OAuth locale.".to_string());
    }
    if let Ok(mut current_cancel) = callback.cancel.lock() {
        if let Some(previous) = current_cancel.replace(cancel.clone()) {
            previous.store(true, Ordering::Release);
        }
    } else {
        return Err("Impossibile preparare la cancellazione del callback OAuth.".to_string());
    }
    let query_state = callback.inner().clone();

    std::thread::spawn(move || {
        use std::io::{Read, Write};
        while !cancel.load(Ordering::Acquire) {
            let Ok((mut stream, _)) = listener.accept() else {
                std::thread::sleep(std::time::Duration::from_millis(25));
                continue;
            };
            let mut buffer = [0_u8; 8192];
            let Ok(size) = stream.read(&mut buffer) else { continue };
            let request = String::from_utf8_lossy(&buffer[..size]);
            let target = request
                .lines()
                .next()
                .and_then(|line| line.split_whitespace().nth(1));
            let Some(target) = target else { continue };
            let Some((path, query)) = target.split_once('?') else {
                let _ = stream.write_all(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
                continue;
            };
            if path != "/callback" {
                let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");
                continue;
            }
            let params: std::collections::HashMap<String, String> =
                url::form_urlencoded::parse(query.as_bytes()).into_owned().collect();
            if !params.contains_key("code") && !params.contains_key("error") {
                let _ = stream.write_all(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n");
                continue;
            }
            if let Ok(mut stored) = query_state.query.lock() {
                *stored = Some(query.to_string());
            }
            let body = "<html><body style=\"font-family:sans-serif;background:#0b0d12;color:white;text-align:center;padding:48px\"><h2>EzTicket</h2><p>Autenticazione completata. Puoi tornare all'app.</p></body></html>";
            let response = format!("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
            let _ = stream.write_all(response.as_bytes());
            break;
        }
    });
    Ok(())
}

#[tauri::command]
fn cancel_oauth_callback(callback: tauri::State<'_, Arc<OAuthCallbackState>>) {
    if let Ok(current_cancel) = callback.cancel.lock() {
        if let Some(cancel) = current_cancel.as_ref() {
            cancel.store(true, Ordering::Release);
        }
    }
}

#[tauri::command]
fn wait_oauth_callback(
    callback: tauri::State<'_, Arc<OAuthCallbackState>>,
) -> Result<std::collections::HashMap<String, String>, String> {
    for _ in 0..300 {
        let mut stored = callback
            .query
            .lock()
            .map_err(|_| "Impossibile leggere il callback OAuth.".to_string())?;
        if let Some(query) = stored.take() {
            return Ok(url::form_urlencoded::parse(query.as_bytes()).into_owned().collect());
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    if let Ok(current_cancel) = callback.cancel.lock() {
        if let Some(cancel) = current_cancel.as_ref() {
            cancel.store(true, Ordering::Release);
        }
    }
    Err("Timeout durante il callback OAuth.".to_string())
}
