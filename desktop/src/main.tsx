import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
/* Layout first, then theme palettes last so [data-theme] wins over :root. */
import './satpuda.css'
import './styles.css'
import './themes.generated.css'
import { IS_DEMO, installDemoMode } from './demoMode'
import {
  applyFontSize,
  applyTheme,
  applyThemePack,
  loadStoredFontSize,
  loadStoredThemePack,
} from './theme'

// First paint: light navy (product default). Disk/meta in App.tsx wins after boot.
// Do not restore a stale localStorage theme here — it caused crimson flashes.
applyTheme('navy-light')
applyThemePack(loadStoredThemePack())
applyFontSize(loadStoredFontSize())

function start() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

// The demo build answers every /api/ call from a recorded snapshot, so the
// interceptor has to be in place before the first screen asks for anything.
if (IS_DEMO) {
  void installDemoMode().then(start)
} else {
  start()
}
