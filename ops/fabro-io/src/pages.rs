//! Split a file into pages of at most 49152 bytes, at line boundaries, deterministically
//! (C3, Decision 4).
//!
//! Part boundaries depend only on the file's bytes, never on the call, so pages can be
//! requested in any order. The concatenation of all parts is byte-identical to the file.

/// The page budget in bytes (ADR 0016, Decision 4, confirmed by task 01).
pub const BUDGET: usize = 49152;

/// One page of a file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Part {
    pub bytes: Vec<u8>,
    /// True when this page is a mid-line cut of a single line longer than the budget.
    pub line_split: bool,
}

/// Split `bytes` into pages of at most `budget` bytes at line boundaries.
///
/// A final line without a trailing newline is a line on its own. A single line longer
/// than the budget is split at a UTF-8 character boundary into consecutive pages, each
/// marked `line_split`.
pub fn split(bytes: &[u8], budget: usize) -> Vec<Part> {
    let budget = budget.max(1);
    let mut parts: Vec<Part> = Vec::new();
    let mut current: Vec<u8> = Vec::new();
    let mut current_line_split = false;

    let mut lines = LineIter::new(bytes);
    while let Some(line) = lines.next() {
        let fits_without_line = current.len() + line.len() <= budget;
        let fits_alone = line.len() <= budget;

        if current.is_empty() {
            if fits_without_line {
                current = line.to_vec();
                current_line_split = false;
            } else {
                // One line that alone exceeds the budget: split it at char boundaries.
                for chunk in utf8_chunks(line, budget) {
                    parts.push(Part { bytes: chunk, line_split: true });
                }
                current.clear();
                current_line_split = false;
            }
        } else if fits_without_line {
            current.extend_from_slice(line);
        } else {
            // Current page is full; close it and start the next with this line.
            parts.push(Part {
                bytes: std::mem::take(&mut current),
                line_split: current_line_split,
            });
            if fits_alone {
                current = line.to_vec();
                current_line_split = false;
            } else {
                for chunk in utf8_chunks(line, budget) {
                    parts.push(Part { bytes: chunk, line_split: true });
                }
            }
        }
    }
    if !current.is_empty() {
        parts.push(Part { bytes: current, line_split: current_line_split });
    }
    if parts.is_empty() {
        // Empty file: one empty page.
        parts.push(Part { bytes: Vec::new(), line_split: false });
    }
    parts
}

/// Iterate the lines of `bytes`, each including its trailing `\n` except a final line
/// without one.
struct LineIter<'a> {
    rest: &'a [u8],
}

impl<'a> LineIter<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { rest: bytes }
    }
    fn next(&mut self) -> Option<&'a [u8]> {
        if self.rest.is_empty() {
            return None;
        }
        if let Some(pos) = self.rest.iter().position(|&b| b == b'\n') {
            let (line, after) = self.rest.split_at(pos + 1);
            self.rest = after;
            Some(line)
        } else {
            let line = self.rest;
            self.rest = &[];
            Some(line)
        }
    }
}

/// Split `bytes` into chunks of at most `budget` bytes at UTF-8 character boundaries.
fn utf8_chunks(bytes: &[u8], budget: usize) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    let mut start = 0;
    while start < bytes.len() {
        let mut end = (start + budget).min(bytes.len());
        // Do not split a multi-byte UTF-8 sequence.
        while end < bytes.len() && !char_boundary(bytes, end) {
            end -= 1;
        }
        out.push(bytes[start..end].to_vec());
        start = end;
    }
    out
}

/// Is `i` a UTF-8 character boundary in `bytes`?
pub fn char_boundary(bytes: &[u8], i: usize) -> bool {
    std::str::from_utf8(&bytes[..i]).is_ok()
}
