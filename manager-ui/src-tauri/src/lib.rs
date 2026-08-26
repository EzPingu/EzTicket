#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(std::sync::Arc::new(std::sync::Mutex::new(None::<String>)))
        .invoke_handler(tauri::generate_handler![start_oauth_callback, wait_oauth_callback])
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[tauri::command]
fn start_oauth_callback(
    callback: tauri::State<'_, std::sync::Arc<std::sync::Mutex<Option<String>>>>,
) -> Result<(), String> {
    let listener = std::net::TcpListener::bind("127.0.0.1:8765")
        .map_err(|error| format!("Impossibile avviare il callback OAuth locale: {error}"))?;
    let callback = callback.inner().clone();
    if let Ok(mut stored) = callback.lock() {
        *stored = None;
    } else {
        return Err("Impossibile preparare il callback OAuth locale.".to_string());
    }

    std::thread::spawn(move || {
        use std::io::{Read, Write};
        let Ok((mut stream, _)) = listener.accept() else { return };
        let mut buffer = [0_u8; 8192];
        let Ok(size) = stream.read(&mut buffer) else { return };
        let request = String::from_utf8_lossy(&buffer[..size]);
        let path = request
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .unwrap_or("/");
        let query = path.split_once('?').map(|(_, value)| value).unwrap_or("");
        if let Ok(mut stored) = callback.lock() {
            *stored = Some(query.to_string());
        }
        let body = "<html><body style=\"font-family:sans-serif;background:#0b0d12;color:white;text-align:center;padding:48px\"><h2>EzTicket</h2><p>Autenticazione completata. Puoi tornare all'app.</p></body></html>";
        let response = format!("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
        let _ = stream.write_all(response.as_bytes());
    });
    Ok(())
}

#[tauri::command]
fn wait_oauth_callback(
    callback: tauri::State<'_, std::sync::Arc<std::sync::Mutex<Option<String>>>>,
) -> Result<std::collections::HashMap<String, String>, String> {
    for _ in 0..300 {
        let mut stored = callback
            .lock()
            .map_err(|_| "Impossibile leggere il callback OAuth.".to_string())?;
        if let Some(query) = stored.take() {
            return Ok(url::form_urlencoded::parse(query.as_bytes()).into_owned().collect());
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    Err("Timeout durante il callback OAuth.".to_string())
}
