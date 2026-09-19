/**
 * Mapped drive F: -> \\192.168.1.1\D. Vite's realpath.native() returns UNC,
 * then normalizePath becomes /192.168.1.1/d/... and every module 404s.
 * Patch native realpath to keep the F: letter. Import this before 'vite'.
 */
import fs from 'node:fs'

function toDrive(p) {
  if (typeof p !== 'string') return p
  return p
    .replace(/^\\\\192\.168\.1\.1\\[dD]/i, 'F:')
    .replace(/^\/\/192\.168\.1\.1\/[dD]/i, 'F:')
}

const nativeSync = fs.realpathSync.native.bind(fs.realpathSync)
fs.realpathSync.native = (p, o) => toDrive(nativeSync(p, o))

const nativePromise = fs.promises.realpath.bind(fs.promises)
fs.promises.realpath = (p, o) => nativePromise(p, o).then(toDrive)
