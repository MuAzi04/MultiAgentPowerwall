//! Connects to the upstream event stream and shows it live in a browser.
//!
//!     cargo run -- [ws://localhost:8765/ws]
//! then open http://localhost:3000

mod communication;

use std::time::Duration;

use axum::{
    extract::{
        ws::{Message as WsMessage, WebSocket, WebSocketUpgrade},
        State,
    },
    response::{Html, Response},
    routing::get,
    Router,
};
use communication::{AnyEvent, Envelope, Event};
use futures_util::StreamExt;
use serde::Serialize;
use tokio::sync::broadcast::{self, error::RecvError};
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{protocol::WebSocketConfig, Message},
};

// Embedded at compile time. Path is relative to this source file,
// so "../index.html" works for both src/main.rs and examples/client.rs.

const INDEX_HTML: &str = include_str!("../static/index.html");
/// What gets sent to the browser.
#[derive(Serialize)]
struct Out {
    delta: bool, // true = append to the current streaming text, false = new line
    text: String,
}

fn handle_event(raw: &str, tx: &broadcast::Sender<String>) {
    let env: Envelope = match serde_json::from_str(raw) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("[bad event: {e}]");
            return;
        }
    };

    let out = if let AnyEvent::Known(Event::BlockDelta { text }) = &env.event {
        Out { delta: true, text: text.clone() }
    }  else {
        let agent = env.agent_id.as_deref().filter(|a| !a.is_empty()).unwrap_or("-");
        let replay = if env.replay { 'R' } else { ' ' };
        Out {
            delta: false,
            text: format!(
                "[{:>6}] {} {:<16} {:<22} {}",
                env.seq,
                replay,
                env.event.type_name(),
                agent,
                env.event.detail()
            ),
        }
    };

    // Err just means no browser is connected right now.
    let _ = tx.send(serde_json::to_string(&out).unwrap());
}

async fn run_upstream(url: String, tx: broadcast::Sender<String>) {
    let config = WebSocketConfig::default().max_message_size(Some(16 * 1024 * 1024));
    let mut backoff = Duration::from_millis(500);

    loop {
        match connect_async_with_config(&url, Some(config), false).await {
            Ok((mut ws, _resp)) => {
                backoff = Duration::from_millis(500);
                while let Some(msg) = ws.next().await {
                    match msg {
                        Ok(Message::Text(t)) => handle_event(t.as_str(), &tx),
                        Ok(Message::Close(_)) | Err(_) => break,
                        Ok(_) => {} // ping/pong handled by the library
                    }
                }
            }
            Err(e) => eprintln!("[connect failed: {e}]"),
        }

        tokio::time::sleep(backoff).await;
        backoff = (backoff * 2).min(Duration::from_secs(10));
    }
}

async fn index() -> Html<&'static str> {
    Html(INDEX_HTML)
}

async fn ws_handler(ws: WebSocketUpgrade, State(tx): State<broadcast::Sender<String>>) -> Response {
    ws.on_upgrade(move |socket| browser_session(socket, tx.subscribe()))
}

async fn browser_session(mut socket: WebSocket, mut rx: broadcast::Receiver<String>) {
    loop {
        tokio::select! {
            msg = rx.recv() => match msg {
                Ok(text) => {
                    if socket.send(WsMessage::Text(text.into())).await.is_err() {
                        break;
                    }
                }
                Err(RecvError::Lagged(_)) => continue, // slow browser; skip ahead
                Err(RecvError::Closed) => break,
            },
            incoming = socket.recv() => match incoming {
                Some(Ok(WsMessage::Close(_))) | Some(Err(_)) | None => break,
                _ => {}
            },
        }
    }
}

#[tokio::main]
async fn main() {
    let url = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "ws://localhost:8765/ws".to_string());

    let (tx, _) = broadcast::channel::<String>(1024);
    tokio::spawn(run_upstream(url, tx.clone()));

    let app = Router::new()
        .route("/", get(index))
        .route("/ws", get(ws_handler))
        .with_state(tx);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:3000").await.unwrap();
    println!("open http://localhost:3000");
    axum::serve(listener, app).await.unwrap();
}