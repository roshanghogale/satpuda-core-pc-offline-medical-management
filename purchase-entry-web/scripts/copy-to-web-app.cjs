const fs = require('fs')
const path = require('path')

const dist = path.join(__dirname, '..', 'dist')
const webApp = path.join(__dirname, '..', '..', 'web_app')

fs.mkdirSync(webApp, { recursive: true })

const indexSrc = path.join(dist, 'index.html')
const indexDst = path.join(webApp, 'index.html')
if (!fs.existsSync(indexSrc)) {
  console.error('Missing dist/index.html — run vite build first.')
  process.exit(1)
}
fs.copyFileSync(indexSrc, indexDst)
console.log('Copied dist/index.html -> web_app/index.html')

const medsSrc = path.join(dist, 'medicines.json')
const medsDst = path.join(webApp, 'medicines.json')
if (fs.existsSync(medsSrc)) {
  fs.copyFileSync(medsSrc, medsDst)
  console.log('Copied dist/medicines.json -> web_app/medicines.json')
}

const assetsSrc = path.join(dist, 'assets')
const assetsDst = path.join(webApp, 'assets')
if (fs.existsSync(assetsSrc)) {
  fs.rmSync(assetsDst, { recursive: true, force: true })
  fs.cpSync(assetsSrc, assetsDst, { recursive: true })
  console.log('Copied dist/assets -> web_app/assets')
}
