//! A canned-response HTTP stub on `std::net::TcpListener`.
//!
//! `box-claim` and `box-guard` speak plain HTTP to fabro's run API and to a box's
//! `/health`, so their shell is tested against a real socket rather than a new
//! dev-dependency. Each test builds its own stub, so a route that never answers
//! ([`Reply::Hang`]) only blocks the connection it is given.

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

/// How long an idle keep-alive connection waits for its next request.
const KEEP_ALIVE: Duration = Duration::from_secs(20);

/// What a route answers.
#[derive(Debug, Clone)]
pub enum Reply {
    /// A status code and a body.
    Json(u16, String),
    /// A status code and an empty body.
    Status(u16),
    /// Accept the request and never answer, so the client's timeout fires.
    Hang,
}

/// One route: the first match on method and path prefix wins.
#[derive(Debug, Clone)]
pub struct Route {
    /// The HTTP method to match.
    pub method: &'static str,
    /// The path prefix to match.
    pub path: String,
    /// The answer.
    pub reply: Reply,
}

impl Route {
    /// A `GET` route.
    pub fn get(path: impl Into<String>, reply: Reply) -> Self {
        Self { method: "GET", path: path.into(), reply }
    }

    /// A `POST` route.
    pub fn post(path: impl Into<String>, reply: Reply) -> Self {
        Self { method: "POST", path: path.into(), reply }
    }
}

/// A running stub. Dropping it stops the accept loop.
pub struct Stub {
    /// The base URL, `http://127.0.0.1:<port>`, with no trailing slash.
    pub url: String,
    log: Arc<Mutex<Vec<(String, String)>>>,
    stop: Arc<AtomicBool>,
}

impl Stub {
    /// Bind a free port and answer `routes` until dropped. Anything else is a 404.
    pub fn start(routes: Vec<Route>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind a free port");
        let port = listener.local_addr().expect("local addr").port();
        listener.set_nonblocking(true).expect("non-blocking listener");
        let log = Arc::new(Mutex::new(Vec::new()));
        let stop = Arc::new(AtomicBool::new(false));
        let accept_log = Arc::clone(&log);
        let accept_stop = Arc::clone(&stop);
        thread::spawn(move || {
            while !accept_stop.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        let log = Arc::clone(&accept_log);
                        let routes = routes.clone();
                        thread::spawn(move || answer(stream, &routes, &log));
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(5));
                    }
                    Err(_) => break,
                }
            }
        });
        Self { url: format!("http://127.0.0.1:{port}"), log, stop }
    }

    /// Every request the stub has seen, as `(method, path)` in arrival order.
    pub fn requests(&self) -> Vec<(String, String)> {
        self.log.lock().expect("stub log").clone()
    }

    /// How many requests the stub has seen.
    pub fn count(&self) -> usize {
        self.log.lock().expect("stub log").len()
    }
}

impl Drop for Stub {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
    }
}

/// Serve requests on one connection until the client closes it or goes quiet.
///
/// Two things here are not incidental. The socket is forced back to blocking mode,
/// because on macOS an accepted socket inherits the listener's `O_NONBLOCK` and every
/// read would then fail with `EAGAIN` before the request arrived. And the loop serves
/// several requests on one connection, because ureq pools connections: closing after
/// the first response made the pool's next request fail at random.
fn answer(mut stream: TcpStream, routes: &[Route], log: &Mutex<Vec<(String, String)>>) {
    if stream.set_nonblocking(false).is_err() {
        return;
    }
    let _ = stream.set_read_timeout(Some(KEEP_ALIVE));
    let Ok(reader_stream) = stream.try_clone() else {
        return;
    };
    let mut reader = BufReader::new(reader_stream);
    loop {
        let mut request_line = String::new();
        match reader.read_line(&mut request_line) {
            Ok(0) | Err(_) => return,
            Ok(_) => {}
        }
        let request_line = request_line.trim_end().to_string();
        if request_line.is_empty() {
            continue;
        }
        loop {
            let mut header = String::new();
            match reader.read_line(&mut header) {
                Ok(0) | Err(_) => return,
                Ok(_) if header == "\r\n" || header == "\n" => break,
                Ok(_) => {}
            }
        }
        let mut parts = request_line.split_whitespace();
        let method = parts.next().unwrap_or_default().to_string();
        let path = parts.next().unwrap_or_default().to_string();
        log.lock().expect("stub log").push((method.clone(), path.clone()));

        let reply = routes
            .iter()
            .find(|route| route.method == method && path.starts_with(&route.path))
            .map(|route| route.reply.clone());
        match reply {
            Some(Reply::Hang) => {
                thread::sleep(Duration::from_secs(30));
                return;
            }
            Some(Reply::Json(status, body)) => write_response(&mut stream, status, &body),
            Some(Reply::Status(status)) => write_response(&mut stream, status, ""),
            None => write_response(&mut stream, 404, ""),
        }
    }
}

/// Write one HTTP/1.1 response with a `Content-Length`, keeping the connection open.
fn write_response(stream: &mut TcpStream, status: u16, body: &str) {
    let reason = match status {
        200 => "OK",
        202 => "Accepted",
        404 => "Not Found",
        409 => "Conflict",
        _ => "Status",
    };
    let response = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Length: {}\r\nContent-Type: application/json\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.flush();
}
