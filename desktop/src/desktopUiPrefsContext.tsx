import { createContext, useContext, type ReactNode } from 'react'
import type { DesktopUiPrefs } from './api'

const DEFAULT_DESKTOP_UI_PREFS: DesktopUiPrefs = {
  show_nav_shortcut_keys: true,
  theme_pack: 'modern',
  table_text_marquee: false,
}

const DesktopUiPrefsContext = createContext<DesktopUiPrefs>(
  DEFAULT_DESKTOP_UI_PREFS,
)

export function DesktopUiPrefsProvider({
  prefs,
  children,
}: {
  prefs: DesktopUiPrefs
  children: ReactNode
}) {
  return (
    <DesktopUiPrefsContext.Provider value={prefs}>
      {children}
    </DesktopUiPrefsContext.Provider>
  )
}

export function useDesktopUiPrefs() {
  return useContext(DesktopUiPrefsContext)
}
