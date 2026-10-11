// Conversation history and theme, kept in this browser only. Every access is guarded:
// storage can be unavailable (private windows, blocked site data) and the app must still work.

const KEY = 'querypilot.conversations.v1'
const THEME_KEY = 'querypilot.theme'

export function loadConversations() {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function saveConversations(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, 30)))
  } catch { /* storage full or blocked: history just won't persist */ }
}

export function loadTheme() {
  try { return localStorage.getItem(THEME_KEY) } catch { return null }
}

export function saveTheme(theme) {
  try { localStorage.setItem(THEME_KEY, theme) } catch { /* ignore */ }
}

export function newId() {
  return 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
}
