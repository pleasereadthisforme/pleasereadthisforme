use std::path::Path;
use std::sync::OnceLock;
use std::sync::atomic::{AtomicBool, Ordering};
static KOREAN: AtomicBool = AtomicBool::new(false);
static MESSAGES: OnceLock<serde_json::Value> = OnceLock::new();
pub fn initialize(settings: &Path, resources: &Path) {
    let saved = std::fs::read_to_string(settings.join("ui-language.txt")).unwrap_or_default();
    let installed = std::fs::read_to_string(resources.join("installer-language.txt")).unwrap_or_default();
    let language = resolve(&saved, &installed);
    set_language(language);
}
pub fn language() -> &'static str { if KOREAN.load(Ordering::Relaxed) { "ko" } else { "en" } }
pub fn set_language(value: &str) { KOREAN.store(value == "ko", Ordering::Relaxed); }
pub fn english() -> bool { language() == "en" }
pub fn text(value: &str) -> &str {
    if !english() { return value; }
    let messages = MESSAGES.get_or_init(|| serde_json::from_str(include_str!(concat!(env!("OUT_DIR"), "/native-messages.json"))).expect("Valid UI translations"));
    messages.get(value).and_then(serde_json::Value::as_str).unwrap_or(value)
}

fn resolve(saved: &str, installed: &str) -> &'static str {
    match saved.trim() {
        "ko" => "ko",
        "en" => "en",
        _ if installed.trim() == "1042" => "ko",
        _ => "en",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn saved_choice_wins_and_installer_language_is_the_initial_default() {
        assert_eq!(resolve("en", "1042"), "en");
        assert_eq!(resolve("ko", "1033"), "ko");
        assert_eq!(resolve("", "1042\r\n"), "ko");
        assert_eq!(resolve("", "1033"), "en");
        assert_eq!(resolve("unknown", ""), "en");
    }
}
