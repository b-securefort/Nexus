import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { applyStoredTheme } from './theme'

// Set data-theme on <html> before first paint (CSP forbids inline scripts,
// so this module-load call is the earliest hook available).
applyStoredTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
