/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DESKTOP_API?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
