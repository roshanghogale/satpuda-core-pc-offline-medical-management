import React, { useState, useEffect } from 'react'
import PurchasePage from './pages/PurchasePage.jsx'
import SupplierConfigPage from './pages/SupplierConfigPage.jsx'
import { loadRuntimeCatalog, getCatalogSnapshot } from './store.js'
import { loadBuiltInMedicines, getLoadedMedicineCount } from './medicineIndex.js'
import styles from './App.module.css'

export default function App() {
  const [page, setPage] = useState('purchase') // 'purchase' | 'config'
  const [connected, setConnected] = useState(false)
  const [medicineCount, setMedicineCount] = useState(0)
  const [catalog, setCatalog] = useState(() => getCatalogSnapshot())
  // Who served the page: the PC's Mobile Import receiver (a phone that scanned the QR) or
  // the PC's own Web Entry. The receiver also says whether the PC's store is wholesale.
  const [store, setStore] = useState({ phone: false, storeKind: 'retail', storeName: '', postTo: '/mobile-import', margins: { A: 8, B: 10, C: 12 } })

  useEffect(() => {
    loadRuntimeCatalog().then(result => {
      setConnected(result.connected)
      setCatalog({
        suppliers: result.suppliers,
        schedules: result.schedules,
        medTypes: result.medTypes,
      })
      setStore(s => ({
        ...s,
        phone: Boolean(result.phone),
        storeKind: result.storeKind || 'retail',
        storeName: result.storeName || '',
        postTo: result.postTo || s.postTo,
        margins: result.margins || s.margins,
      }))
    })
    loadBuiltInMedicines().then(() => setMedicineCount(getLoadedMedicineCount()))
  }, [])

  const wholesale = store.storeKind === 'wholesale'

  return (
    <div className={styles.app}>
      {!connected && (
        <div className={styles.connectBanner}>
          Open <strong>Settings → Open Web Purchase Entry</strong> from the desktop app (writes catalog.json with suppliers).
        </div>
      )}
      {connected && store.phone && (
        <div className={`${styles.connectBanner} ${wholesale ? styles.wholesaleBanner : styles.okBanner}`}>
          Connected to the PC{store.storeName ? ` · ${store.storeName}` : ''} ·{' '}
          <strong>{wholesale ? 'Wholesale store' : 'Retail store'}</strong>
          {wholesale
            ? ' — strips, purchase rate per strip, MRP optional, Rate A / B / C (Rate A required).'
            : ''}
          {' '}Bills you send appear on the PC in Settings → Import → Mobile Import.
        </div>
      )}
      {connected && !store.phone && catalog.suppliers.length > 0 && (
        <div className={`${styles.connectBanner} ${styles.okBanner}`}>
          Synced {catalog.suppliers.length} suppliers · {catalog.schedules.length} schedules · {catalog.medTypes.length} types
        </div>
      )}
      <nav className={styles.nav}>
        <span className={styles.brand}>📦 Purchase Entry{wholesale ? ' · Wholesale' : ''}</span>
        <div className={styles.navLinks}>
          <button
            className={page === 'purchase' ? styles.active : ''}
            onClick={() => setPage('purchase')}
          >
            Purchase Entry
          </button>
          {/* The column layouts there are the retail columns; a wholesale line has a fixed one. */}
          {!wholesale && (
            <button
              className={page === 'config' ? styles.active : ''}
              onClick={() => setPage('config')}
            >
              ⚙ Settings
            </button>
          )}
        </div>
        <span className={styles.hint}>
          {medicineCount > 0
            ? `${medicineCount.toLocaleString()} medicines (built-in) · `
            : ''}
          Ctrl+N = New Bill &nbsp;|&nbsp; Ctrl+S = Copy JSON
        </span>
      </nav>
      <main className={styles.main}>
        {page === 'purchase' || wholesale ? (
          <PurchasePage
            suppliers={catalog.suppliers}
            schedules={catalog.schedules}
            medicineTypes={catalog.medTypes}
            phone={store.phone}
            wholesale={wholesale}
            postTo={store.postTo}
            margins={store.margins}
          />
        ) : (
          <SupplierConfigPage />
        )}
      </main>
    </div>
  )
}
